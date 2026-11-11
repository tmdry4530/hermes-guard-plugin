"""AgentPay Guard plugin entry point."""
from __future__ import annotations


def register(ctx) -> None:
    """Register guard hooks, commands, and read-only policy tool."""
    from plugins.plugin_storage import plugin_data_dir
    try:
        from .store import Store
        from .hooks import create_hooks
        from .commands import register as register_commands
        from .tools import SCHEMA, make_handler
    except ImportError:
        from store import Store
        from hooks import create_hooks
        from commands import register as register_commands
        from tools import SCHEMA, make_handler
    store = Store(plugin_data_dir("agentpay-guard"))
    pre, post, approval, policy = create_hooks(ctx, store)
    ctx.register_hook("pre_tool_call", pre)
    ctx.register_hook("post_tool_call", post)
    ctx.register_hook("post_approval_response", approval)
    register_commands(ctx, store, policy)
    ctx.register_tool(name="guard_check", toolset="agentpay_guard", schema=SCHEMA,
                      handler=make_handler(policy, store),
                      description="Dry-run AgentPay Guard policy for a hypothetical tool call; cannot change state.",
                      emoji="🛡️")
