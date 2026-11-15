// x86_64 replacements for native_call_arm64.S.
//
// On AArch64 a by-value class result is returned through x8 (indirect result register), so the
// probe needed assembly to load x8 before `blr`. On x86_64 System V the same hidden result pointer
// is the first integer argument (rdi), ahead of the explicit arguments, so these become plain
// C++ calls with the result pointer moved to the front. Signatures match cr_replay_probe.cpp:272.
#if defined(__x86_64__)
#include <cstdint>

extern "C" {

void cr_call_logic_command_parse(void *json_object, void *command_context, void **result, void *target) {
  reinterpret_cast<void (*)(void **, void *, void *)>(target)(result, json_object, command_context);
}

void cr_call_state_snapshot(void *manager, void **result, void *target) {
  reinterpret_cast<void (*)(void **, void *)>(target)(result, manager);
}

void cr_call_card_selection_build(void *battle_deck_slot, void *player, std::int32_t alternate, void *result,
                                  void *target) {
  reinterpret_cast<void (*)(void *, void *, void *, std::int32_t)>(target)(result, battle_deck_slot, player,
                                                                         alternate);
}

}  // extern "C"

// cr_combat_heal_entry: target of the inline hook on CombatHeal (x86 0x1112140).
// Entry state (jumped to from the hooked prologue): rdi,rsi,edx,rcx,r8d,r9d = args 1-6,
// [rsp] = caller return address, [rsp+8] = arg 7. On the projectile call site (return 0x10d94d7)
// the projectile object lives in rbx, exactly where ARM keeps it in x19 (verified: the three
// getter calls before the heal use `mov %rbx,%rdi` on x86 and `mov x0,x19` on ARM).
// Calls cr_combat_heal_bridge(a1..a6, a7, caller_x19=rbx, caller_frame=rbp, caller_return).
asm(R"(
    .text
    .globl cr_combat_heal_entry
    .type cr_combat_heal_entry, @function
cr_combat_heal_entry:
    movq (%rsp), %rax
    movq 8(%rsp), %r10
    subq $40, %rsp
    movq %r10, 0(%rsp)
    movq %rbx, 8(%rsp)
    movq %rbp, 16(%rsp)
    movq %rax, 24(%rsp)
    call cr_combat_heal_bridge
    addq $40, %rsp
    ret
    .size cr_combat_heal_entry, .-cr_combat_heal_entry

    // ---- remaining-runtime entries (x86 equivalents of remaining_runtime_arm64.S) ----
    // Resource producers: (resource=rdi, amount=esi, accounting=edx) + caller return + source.
    // The source object lives in rbx at all three call sites (ARM keeps it in x19; verified: periodic
    // producer rbx=this like ARM x19=x0; death producers rbx=[this+8] like ARM x19=[x0+8]).
    .globl remaining_resource_whole_entry
    .type remaining_resource_whole_entry, @function
remaining_resource_whole_entry:
    movq (%rsp), %rcx
    movq %rbx, %r8
    jmp remaining_resource_whole_bridge
    .size remaining_resource_whole_entry, .-remaining_resource_whole_entry

    .globl remaining_resource_fixed_entry
    .type remaining_resource_fixed_entry, @function
remaining_resource_fixed_entry:
    movq (%rsp), %rcx
    movq %rbx, %r8
    jmp remaining_resource_fixed_bridge
    .size remaining_resource_fixed_entry, .-remaining_resource_fixed_entry

    // SpawnAttach helper: preserve every argument register, observe, then continue in the original.
    .globl remaining_spawn_helper_entry
    .type remaining_spawn_helper_entry, @function
remaining_spawn_helper_entry:
    subq $0xb8, %rsp
    movq %rdi, 0x00(%rsp)
    movq %rsi, 0x08(%rsp)
    movq %rdx, 0x10(%rsp)
    movq %rcx, 0x18(%rsp)
    movq %r8, 0x20(%rsp)
    movq %r9, 0x28(%rsp)
    movq %rax, 0x30(%rsp)
    movdqu %xmm0, 0x38(%rsp)
    movdqu %xmm1, 0x48(%rsp)
    movdqu %xmm2, 0x58(%rsp)
    movdqu %xmm3, 0x68(%rsp)
    movdqu %xmm4, 0x78(%rsp)
    movdqu %xmm5, 0x88(%rsp)
    movdqu %xmm6, 0x98(%rsp)
    movdqu %xmm7, 0xa8(%rsp)
    movq 0xb8(%rsp), %rcx
    call remaining_spawn_helper_begin_bridge
    movdqu 0xa8(%rsp), %xmm7
    movdqu 0x98(%rsp), %xmm6
    movdqu 0x88(%rsp), %xmm5
    movdqu 0x78(%rsp), %xmm4
    movdqu 0x68(%rsp), %xmm3
    movdqu 0x58(%rsp), %xmm2
    movdqu 0x48(%rsp), %xmm1
    movdqu 0x38(%rsp), %xmm0
    movq 0x30(%rsp), %rax
    movq 0x28(%rsp), %r9
    movq 0x20(%rsp), %r8
    movq 0x18(%rsp), %rcx
    movq 0x10(%rsp), %rdx
    movq 0x08(%rsp), %rsi
    movq 0x00(%rsp), %rdi
    addq $0xb8, %rsp
    movq g_original_remaining_spawn_helper@GOTPCREL(%rip), %r11
    jmp *(%r11)
    .size remaining_spawn_helper_entry, .-remaining_spawn_helper_entry

    // ActionSpawn mode-1 child create: bridge(manager=rdi, child=rsi, option=edx, commit=ecx,
    // caller_return, ambient_source=r15 (ARM x21 = [arg1+8] == x86 r15 = [rsi+8] in the caller),
    // request_plus_0x48 = 0 for now), then continue in the original with all registers restored.
    .globl remaining_action_spawn_mode1_child_create_entry
    .type remaining_action_spawn_mode1_child_create_entry, @function
remaining_action_spawn_mode1_child_create_entry:
    subq $0xb8, %rsp
    movq %rdi, 0x00(%rsp)
    movq %rsi, 0x08(%rsp)
    movq %rdx, 0x10(%rsp)
    movq %rcx, 0x18(%rsp)
    movq %r8, 0x20(%rsp)
    movq %r9, 0x28(%rsp)
    movq %rax, 0x30(%rsp)
    movdqu %xmm0, 0x38(%rsp)
    movdqu %xmm1, 0x48(%rsp)
    movdqu %xmm2, 0x58(%rsp)
    movdqu %xmm3, 0x68(%rsp)
    movdqu %xmm4, 0x78(%rsp)
    movdqu %xmm5, 0x88(%rsp)
    movdqu %xmm6, 0x98(%rsp)
    movdqu %xmm7, 0xa8(%rsp)
    subq $8, %rsp
    pushq $0
    movq 0xc8(%rsp), %r8
    movq %r15, %r9
    call remaining_action_spawn_mode1_child_create_bridge
    addq $16, %rsp
    movdqu 0xa8(%rsp), %xmm7
    movdqu 0x98(%rsp), %xmm6
    movdqu 0x88(%rsp), %xmm5
    movdqu 0x78(%rsp), %xmm4
    movdqu 0x68(%rsp), %xmm3
    movdqu 0x58(%rsp), %xmm2
    movdqu 0x48(%rsp), %xmm1
    movdqu 0x38(%rsp), %xmm0
    movq 0x30(%rsp), %rax
    movq 0x28(%rsp), %r9
    movq 0x20(%rsp), %r8
    movq 0x18(%rsp), %rcx
    movq 0x10(%rsp), %rdx
    movq 0x08(%rsp), %rsi
    movq 0x00(%rsp), %rdi
    addq $0xb8, %rsp
    movq g_original_remaining_action_spawn_mode1_child_create@GOTPCREL(%rip), %r11
    jmp *(%r11)
    .size remaining_action_spawn_mode1_child_create_entry, .-remaining_action_spawn_mode1_child_create_entry
)");

#endif
