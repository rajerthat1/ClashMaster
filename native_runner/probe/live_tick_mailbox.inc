// Live (server-driven) battle capture and action mailbox.
//
// This is intentionally separate from the headless/native-render paths: the
// stock online client owns the GameStateManager, the probe only observes it.
// `live_tick_mailbox_on_native_boundary` is called from game_state_step_hook
// after the stock step, so a capture is an exact post-step snapshot.
//
// Commands:
//   live-arm                          start capturing every 5-tick boundary
//   live-disarm                       stop capturing
//   live-observe-status               generation/tick/ready flags
//   live-observe                      return the latest atomic capture
//   live-play OWNER HAND X Y CARD DUE  resolve+inject one hand card
//   live-ability OWNER OBJ SECONDARY DUE
//
// The host must feed every capture to the encoder in order; dropping a
// boundary breaks the recurrent episode.

struct LiveTickMailbox {
  std::unique_ptr<char[]> payload;
  std::size_t payload_bytes = 0;
  void *observed_manager = nullptr;
  std::uint64_t generation = 0;
  std::int32_t last_tick = -1;
  std::int64_t last_seen_ns = 0;
  bool armed = false;
  bool ready = false;
  bool capture_ok = false;
  std::uint64_t captured_generation = 0;
  std::int32_t captured_tick = -1;
  std::int64_t encode_ns = 0;
};

LiveTickMailbox g_live_tick_mailbox;

bool build_live_tick_atomic_json_locked(void *manager, std::uint64_t generation,
                                        std::int32_t expected_tick, char *response,
                                        std::size_t response_size) {
  constexpr const char *kSchema = "native-live-tick-capture.v1";
  if (manager == nullptr || response == nullptr ||
      response_size < kFullObservationResponseBytes || g_game_state_tick == nullptr ||
      g_game_state_tick(manager) != expected_tick) {
    return false;
  }
  response[0] = '\0';
  std::size_t used = 0;
  JsonWriter json(response, response_size, used);
  if (!json.append("{\"ok\":true,\"schema\":\"%s\",\"atomic\":true,"
                   "\"source\":\"live-player\",\"generation\":%llu,\"tick\":%d,"
                   "\"ordinary\":",
                   kSchema, static_cast<unsigned long long>(generation), expected_tick)) {
    return false;
  }
  bool ordinary_complete = false;
  if (!build_observation_json(manager, generation, response + used,
                              response_size - used, &ordinary_complete) ||
      !ordinary_complete) {
    return false;
  }
  used += std::strlen(response + used);
  if (g_game_state_tick(manager) != expected_tick || !json.append(",\"rich\":")) {
    return false;
  }
  bool rich_complete = false;
  if (!build_rich_observation_json(manager, generation, response + used,
                                   response_size - used, &rich_complete) ||
      !rich_complete) {
    return false;
  }
  used += std::strlen(response + used);
  return g_game_state_tick(manager) == expected_tick && json.append("}");
}

void live_tick_mailbox_on_native_boundary(void *manager, std::int32_t tick) {
  if (manager == nullptr || tick < 0 || g_game_state_tick == nullptr ||
      g_runner_mode.load(std::memory_order_acquire) !=
          static_cast<std::int32_t>(RunnerMode::Headless)) {
    return;
  }
  pthread_mutex_lock(&g_control_mutex);
  LiveTickMailbox &mailbox = g_live_tick_mailbox;
  if (g_controlled_manager != nullptr || g_resident_mode_active) {
    pthread_mutex_unlock(&g_control_mutex);
    return;
  }
  if (manager != mailbox.observed_manager || tick < mailbox.last_tick) {
    mailbox.observed_manager = manager;
    ++mailbox.generation;
    if (mailbox.generation == 0) {
      ++mailbox.generation;
    }
    mailbox.last_tick = -1;
    mailbox.ready = false;
    mailbox.capture_ok = false;
    mailbox.captured_tick = -1;
  }
  mailbox.last_tick = tick;
  mailbox.last_seen_ns = process_monotonic_now_ns();
  if (!mailbox.armed || tick % 5 != 0) {
    pthread_mutex_unlock(&g_control_mutex);
    return;
  }
  if (mailbox.payload == nullptr) {
    mailbox.payload.reset(new (std::nothrow) char[kFullObservationResponseBytes]());
    mailbox.payload_bytes = mailbox.payload != nullptr ? kFullObservationResponseBytes : 0;
  }
  if (mailbox.payload == nullptr) {
    pthread_mutex_unlock(&g_control_mutex);
    return;
  }
  const std::int64_t started = process_monotonic_now_ns();
  mailbox.capture_ok = build_live_tick_atomic_json_locked(
      manager, mailbox.generation, tick, mailbox.payload.get(), mailbox.payload_bytes);
  mailbox.encode_ns = process_monotonic_now_ns() - started;
  mailbox.captured_generation = mailbox.generation;
  mailbox.captured_tick = tick;
  mailbox.ready = mailbox.capture_ok;
  pthread_cond_broadcast(&g_control_cond);
  pthread_mutex_unlock(&g_control_mutex);
}

bool handle_live_tick_mailbox_command(int socket_fd, const char *command) {
  if (command == nullptr) {
    return false;
  }
  if (std::strcmp(command, "live-observe-status") == 0) {
    char response[512] = {};
    pthread_mutex_lock(&g_control_mutex);
    const LiveTickMailbox &mailbox = g_live_tick_mailbox;
    const std::int64_t age_ns =
        mailbox.last_seen_ns > 0 ? process_monotonic_now_ns() - mailbox.last_seen_ns : -1;
    std::snprintf(response, sizeof(response),
                  "{\"ok\":true,\"schema\":\"live-observe-status.v1\","
                  "\"generation\":%llu,\"tick\":%d,\"active\":%s,"
                  "\"lastSeenAgeNs\":%lld,\"armed\":%s,\"ready\":%s,"
                  "\"capturedGeneration\":%llu,\"capturedTick\":%d,"
                  "\"captureOk\":%s,\"encodeNs\":%lld}",
                  static_cast<unsigned long long>(mailbox.generation), mailbox.last_tick,
                  age_ns >= 0 && age_ns < 1000000000LL ? "true" : "false",
                  static_cast<long long>(age_ns), mailbox.armed ? "true" : "false",
                  mailbox.ready ? "true" : "false",
                  static_cast<unsigned long long>(mailbox.captured_generation),
                  mailbox.captured_tick, mailbox.capture_ok ? "true" : "false",
                  static_cast<long long>(mailbox.encode_ns));
    pthread_mutex_unlock(&g_control_mutex);
    send_control_response(socket_fd, response);
    return true;
  }
  if (std::strcmp(command, "live-arm") == 0) {
    pthread_mutex_lock(&g_control_mutex);
    g_live_tick_mailbox.armed = true;
    g_live_tick_mailbox.ready = false;
    g_live_tick_mailbox.capture_ok = false;
    g_live_tick_mailbox.captured_tick = -1;
    pthread_mutex_unlock(&g_control_mutex);
    send_control_response(socket_fd, "{\"ok\":true,\"armed\":true}");
    return true;
  }
  if (std::strcmp(command, "live-disarm") == 0) {
    pthread_mutex_lock(&g_control_mutex);
    g_live_tick_mailbox.armed = false;
    g_live_tick_mailbox.ready = false;
    pthread_mutex_unlock(&g_control_mutex);
    send_control_response(socket_fd, "{\"ok\":true,\"armed\":false}");
    return true;
  }
  if (std::strcmp(command, "live-observe") == 0) {
    std::unique_ptr<char[]> copy;
    std::size_t bytes = 0;
    pthread_mutex_lock(&g_control_mutex);
    if (g_live_tick_mailbox.ready && g_live_tick_mailbox.payload != nullptr) {
      bytes = std::strlen(g_live_tick_mailbox.payload.get()) + 1;
      copy.reset(new (std::nothrow) char[bytes]());
      if (copy != nullptr) {
        std::memcpy(copy.get(), g_live_tick_mailbox.payload.get(), bytes);
        g_live_tick_mailbox.ready = false;
      }
    }
    pthread_mutex_unlock(&g_control_mutex);
    if (copy == nullptr) {
      send_control_error(socket_fd, "no live capture is ready");
      return true;
    }
    send_control_response(socket_fd, copy.get());
    return true;
  }
  if (std::strncmp(command, "live-play ", 10) == 0) {
    unsigned long long owner = 0;
    int hand = -1;
    int x = 0;
    int y = 0;
    unsigned int card = 0;
    int due = 0;
    char trailing = '\0';
    if (std::sscanf(command + 10, "%llu %d %d %d %u %d%c", &owner, &hand, &x, &y,
                    &card, &due, &trailing) != 6 ||
        owner > 1 || hand < 0 || due < 0) {
      send_control_error(socket_fd, "live-play expects OWNER HAND X Y CARD DUE");
      return true;
    }
    void *manager = nullptr;
    std::int32_t current_tick = -1;
    pthread_mutex_lock(&g_control_mutex);
    manager = g_live_tick_mailbox.observed_manager;
    current_tick = g_live_tick_mailbox.last_tick;
    pthread_mutex_unlock(&g_control_mutex);
    if (manager == nullptr || g_game_state_tick == nullptr ||
        g_game_state_tick(manager) != current_tick) {
      send_control_error(socket_fd, "live battle is not bound");
      return true;
    }
    DeployRequest deploy;
    char command_json[768] = {};
    const bool resolved =
        resolve_hand_deploy_request(manager, static_cast<std::int32_t>(owner), hand, x, y, &deploy) &&
        deploy.root_card_id == card && due > current_tick &&
        format_deploy_command(deploy, due - static_cast<std::int32_t>(kLiveCommandAgeTicks) - 1,
                              command_json, sizeof(command_json)) &&
        inject_logic_command(manager, command_json);
    char response[768] = {};
    if (!resolved) {
      std::snprintf(response, sizeof(response),
                    "{\"ok\":false,\"error\":\"live card play rejected\",\"tick\":%d}", current_tick);
    } else {
      std::snprintf(response, sizeof(response),
                    "{\"ok\":true,\"queuedTick\":%d,\"dueTick\":%d,\"cardId\":%d,"
                    "\"commandCardId\":%u,\"deckSlot\":%d,\"cost\":%d}",
                    current_tick, due, deploy.root_card_id, deploy.card_id, deploy.deck_slot,
                    deploy.cost);
    }
    send_control_response(socket_fd, response);
    return true;
  }
  if (std::strncmp(command, "live-ability ", 13) == 0) {
    unsigned long long owner = 0;
    int object_index = -1;
    int secondary_index = -1;
    int due = 0;
    char trailing = '\0';
    if (std::sscanf(command + 13, "%llu %d %d %d%c", &owner, &object_index,
                    &secondary_index, &due, &trailing) != 4 ||
        owner > 1 || object_index < 0 || due < 0) {
      send_control_error(socket_fd, "live-ability expects OWNER OBJECT SECONDARY DUE");
      return true;
    }
    void *manager = nullptr;
    std::int32_t current_tick = -1;
    pthread_mutex_lock(&g_control_mutex);
    manager = g_live_tick_mailbox.observed_manager;
    current_tick = g_live_tick_mailbox.last_tick;
    pthread_mutex_unlock(&g_control_mutex);
    if (manager == nullptr || g_game_state_tick == nullptr ||
        g_game_state_tick(manager) != current_tick) {
      send_control_error(socket_fd, "live battle is not bound");
      return true;
    }
    AbilityRequest ability;
    char command_json[768] = {};
    const bool resolved =
        resolve_ready_ability_request(manager, static_cast<std::int32_t>(owner), object_index,
                                       secondary_index, &ability) &&
        due > current_tick &&
        format_ability_command(ability, due - 2, command_json, sizeof(command_json)) &&
        inject_logic_command(manager, command_json);
    char response[512] = {};
    if (!resolved) {
      std::snprintf(response, sizeof(response),
                    "{\"ok\":false,\"error\":\"live ability rejected\",\"tick\":%d}", current_tick);
    } else {
      std::snprintf(response, sizeof(response),
                    "{\"ok\":true,\"queuedTick\":%d,\"dueTick\":%d}", current_tick, due);
    }
    send_control_response(socket_fd, response);
    return true;
  }
  return false;
}
