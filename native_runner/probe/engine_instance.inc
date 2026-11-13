#include "probe_json.h"

// Android creates a real zygote child for every manifest component process.
// The cluster APK declares :engine0 through :engine23, so each process receives
// an independent libg address space and therefore an independent execution
// lane while all processes share the same user-0 game data.

char g_engine_process_name[128] = {};

bool engine_instance_read_process_name() {
  FILE *file = std::fopen("/proc/self/cmdline", "rb");
  if (file == nullptr) {
    return false;
  }
  const std::size_t count = std::fread(g_engine_process_name, 1, sizeof(g_engine_process_name) - 1, file);
  std::fclose(file);
  g_engine_process_name[count] = '\0';
  return count != 0;
}

bool engine_instance_parse_process_id(std::int32_t *engine_id) {
  if (engine_id == nullptr || !engine_instance_read_process_name()) {
    return false;
  }
  const char *marker = std::strrchr(g_engine_process_name, ':');
  constexpr char kEnginePrefix[] = ":engine";
  if (marker == nullptr || std::strncmp(marker, kEnginePrefix, sizeof(kEnginePrefix) - 1) != 0) {
    return false;
  }
  const char *value = marker + sizeof(kEnginePrefix) - 1;
  if (*value == '\0') {
    return false;
  }
  errno = 0;
  char *end = nullptr;
  const long parsed = std::strtol(value, &end, 10);
  if (errno != 0 || end == value || end == nullptr || *end != '\0' || parsed < 0 ||
      parsed >= static_cast<long>(kEngineInstanceMaxCount)) {
    return false;
  }
  *engine_id = static_cast<std::int32_t>(parsed);
  return true;
}

bool engine_instance_read_id(const char *path, std::int32_t *engine_id) {
  if (path == nullptr || engine_id == nullptr) {
    return false;
  }
  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    return false;
  }
  int parsed = -1;
  const bool ok =
      std::fscanf(file, "%d", &parsed) == 1 && parsed >= 0 && parsed < static_cast<int>(kEngineInstanceMaxCount);
  std::fclose(file);
  if (ok) {
    *engine_id = parsed;
  }
  return ok;
}

void initialize_engine_instance() {
  constexpr std::int32_t kAndroidUidPerUser = 100000;
  const uid_t uid = getuid();
  const std::int32_t android_user_id = static_cast<std::int32_t>(uid / kAndroidUidPerUser);
  g_engine_android_user_id = android_user_id;

  char engine_id_path[256] = {};
  std::snprintf(engine_id_path, sizeof(engine_id_path), "/data/user/%d/%s/files/cr_engine_id", android_user_id,
                kPackageName);
  std::int32_t engine_id = 0;
  if (!engine_instance_parse_process_id(&engine_id) && !engine_instance_read_id(engine_id_path, &engine_id)) {
    // Fresh Android secondary users start at id 10.  This fallback lets
    // older multi-user development harnesses remain usable.  Production
    // cluster launchers use manifest process names instead.
    if (android_user_id >= 10 && android_user_id < 10 + static_cast<std::int32_t>(kEngineInstanceMaxCount - 1)) {
      engine_id = android_user_id - 9;
    }
  }
  g_engine_instance_id = engine_id;
  g_engine_instance_port = static_cast<std::uint16_t>(kControlPort + engine_id);

  std::snprintf(g_user_fingerprint_path, sizeof(g_user_fingerprint_path), "/data/user/%d/%s/update/fingerprint.json",
                android_user_id, kPackageName);
  std::snprintf(g_user_assets_path, sizeof(g_user_assets_path), "/data/user/%d/%s/update/assets.scdb", android_user_id,
                kPackageName);

  LOGI("engine identity process=%s user=%d uid=%d engine=%d port=%u",
       g_engine_process_name[0] == '\0' ? "<unknown>" : g_engine_process_name, android_user_id, static_cast<int>(uid),
       engine_id, g_engine_instance_port);
}

// The MuMu cluster exposes exactly one vCPU per engine, so engine N -> cpu N is
// the natural isolation mapping.  Physical phones are heterogeneous (the A51
// runs 4x A53 at 1.74 GHz on cpu0-3 and 4x A73 at 2.31 GHz on cpu4-7), so
// binding engine 0 to cpu 0 leaves the control lane on the slow cluster.
// `debug.cr.engine_cpu_map` overrides the mapping without a rebuild:
//   "4,5,6,7" -> engine 0..3 pin to cpu 4..7 (big cluster on the A51)
//   "none"    -> do not pin; let the scheduler place the control thread
// Unset keeps the MuMu-compatible engine N -> cpu N behaviour.
bool engine_instance_read_cpu_override(std::int32_t engine_id, std::int32_t *cpu, bool *disabled) {
  *cpu = -1;
  *disabled = false;
  char value[PROP_VALUE_MAX] = {};
  const int length = __system_property_get("debug.cr.engine_cpu_map", value);
  if (length <= 0) {
    return false;
  }
  if (std::strcmp(value, "none") == 0) {
    *disabled = true;
    return true;
  }
  const char *cursor = value;
  std::int32_t index = 0;
  while (*cursor != '\0') {
    errno = 0;
    char *end = nullptr;
    const long parsed = std::strtol(cursor, &end, 10);
    if (errno != 0 || end == cursor || parsed < 0 || parsed >= CPU_SETSIZE) {
      LOGE("debug.cr.engine_cpu_map has an invalid entry at index %d: %s", index, cursor);
      return false;
    }
    if (index == engine_id) {
      *cpu = static_cast<std::int32_t>(parsed);
      return true;
    }
    ++index;
    if (*end != ',') {
      break;
    }
    cursor = end + 1;
  }
  LOGI("debug.cr.engine_cpu_map has no entry for engine %d; using engine index", engine_id);
  return false;
}

bool engine_instance_apply_affinity(std::int32_t cpu, bool pin) {
  cpu_set_t affinity;
  CPU_ZERO(&affinity);
  if (pin) {
    CPU_SET(cpu, &affinity);
  } else {
    const long online = sysconf(_SC_NPROCESSORS_ONLN);
    for (long index = 0; index < online && index < CPU_SETSIZE; ++index) {
      CPU_SET(index, &affinity);
    }
  }
  if (sched_setaffinity(0, sizeof(affinity), &affinity) != 0) {
    LOGE("engine %d could not set control lane affinity (pin=%d cpu=%d): %d", g_engine_instance_id,
         pin ? 1 : 0, cpu, errno);
    return false;
  }
  g_engine_instance_cpu = pin ? cpu : -1;
  return true;
}

bool engine_instance_pin_current_lane() {
  const long online = sysconf(_SC_NPROCESSORS_ONLN);
  std::int32_t cpu = g_engine_instance_id;
  bool disabled = false;
  const bool overridden = engine_instance_read_cpu_override(g_engine_instance_id, &cpu, &disabled);
  if (disabled) {
    if (!engine_instance_apply_affinity(-1, false)) {
      return false;
    }
    LOGI("engine %d control lane affinity override=none (online=%ld)", g_engine_instance_id, online);
    return true;
  }
  if (!overridden) {
    cpu = g_engine_instance_id;
  }
  if (cpu < 0 || cpu >= online || cpu >= CPU_SETSIZE) {
    LOGE("engine %d cannot bind a lane: %ld CPUs online, requested cpu %d", g_engine_instance_id, online, cpu);
    return false;
  }
  if (!engine_instance_apply_affinity(cpu, true)) {
    return false;
  }
  LOGI("engine %d control lane bound to cpu %d%s (online=%ld)", g_engine_instance_id, cpu,
       overridden ? " via debug.cr.engine_cpu_map" : "", online);
  return true;
}

bool engine_instance_has_resident_slots() {
  for (const auto &slot : g_resident_slots) {
    if (slot.occupied) {
      return true;
    }
  }
  return false;
}

void engine_instance_affinity_text(char *buffer, std::size_t size) {
  if (buffer == nullptr || size == 0) {
    return;
  }
  cpu_set_t affinity;
  CPU_ZERO(&affinity);
  if (sched_getaffinity(0, sizeof(affinity), &affinity) != 0) {
    std::snprintf(buffer, size, "unavailable");
    return;
  }
  std::size_t used = 0;
  buffer[0] = '\0';
  for (int cpu = 0; cpu < CPU_SETSIZE && used + 8 < size; ++cpu) {
    if (!CPU_ISSET(cpu, &affinity)) {
      continue;
    }
    const int written = std::snprintf(buffer + used, size - used, "%s%d", used == 0 ? "" : ",", cpu);
    if (written <= 0) {
      break;
    }
    used += static_cast<std::size_t>(written);
  }
}

void engine_instance_send_status(int socket_fd) {
  char response[1024] = {};
  std::size_t used = 0;
  char affinity_text[256] = {};
  engine_instance_affinity_text(affinity_text, sizeof(affinity_text));
  JsonWriter writer(response, sizeof(response), used);
  writer.begin_object();
  writer.field("ok", true);
  writer.field("version", "engine-instance.v1");
  writer.field("engineId", g_engine_instance_id);
  writer.field("androidUserId", g_engine_android_user_id);
  writer.field("processName", g_engine_process_name[0] ? g_engine_process_name : "<unknown>");
  writer.field("slotsPerEngine", kEngineInstanceSlots);
  writer.field("pid", static_cast<int>(getpid()));
  writer.field("port", g_engine_instance_port);
  writer.field("assignedCpu", g_engine_instance_cpu);
  writer.field("currentCpu", sched_getcpu());
  writer.field("affinity", static_cast<const char *>(affinity_text));
  writer.field("onlineCpus", sysconf(_SC_NPROCESSORS_ONLN));
  writer.field("residentMode", g_resident_mode_active);
  writer.field("occupied", engine_instance_has_resident_slots());
  writer.end_object();
  send_control_response(socket_fd,
                        writer.good() ? response : "{\"ok\":false,\"error\":\"engine status encoding failed\"}");
}

bool handle_engine_instance_command(int socket_fd, const char *command) {
  if (std::strcmp(command, "engine-status") == 0) {
    engine_instance_send_status(socket_fd);
    return true;
  }
  constexpr char kAffinityPrefix[] = "engine-affinity";
  if (std::strncmp(command, kAffinityPrefix, sizeof(kAffinityPrefix) - 1) == 0 &&
      (command[sizeof(kAffinityPrefix) - 1] == ' ' || command[sizeof(kAffinityPrefix) - 1] == '\0')) {
    const char *argument = command + sizeof(kAffinityPrefix) - 1;
    while (*argument == ' ') {
      ++argument;
    }
    const long online = sysconf(_SC_NPROCESSORS_ONLN);
    if (std::strcmp(argument, "none") == 0) {
      if (!engine_instance_apply_affinity(-1, false)) {
        send_control_response(socket_fd, "{\"ok\":false,\"error\":\"affinity update failed\"}");
        return true;
      }
    } else {
      errno = 0;
      char *end = nullptr;
      const long parsed = std::strtol(argument, &end, 10);
      if (errno != 0 || end == argument || *end != '\0' || parsed < 0 || parsed >= online ||
          parsed >= CPU_SETSIZE) {
        send_control_response(socket_fd, "{\"ok\":false,\"error\":\"engine-affinity expects 'none' or an online cpu\"}");
        return true;
      }
      if (!engine_instance_apply_affinity(static_cast<std::int32_t>(parsed), true)) {
        send_control_response(socket_fd, "{\"ok\":false,\"error\":\"affinity update failed\"}");
        return true;
      }
    }
    engine_instance_send_status(socket_fd);
    return true;
  }
  return false;
}
