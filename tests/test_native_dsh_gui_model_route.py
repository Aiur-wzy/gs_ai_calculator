import pytest

from genshin_app.agent_chat.dsh_gui_workflow_runner import (
    GuiWorkflowError,
    _projected_model_selection,
    run_gui_agent_workflow,
)

CATALOG = {"default": {"provider": "deepseek-official", "model": "deepseek-flash", "reasoningEffort": "high"}}


def test_projection_preserves_official_default_and_saved_selection():
    assert _projected_model_selection(CATALOG, {}, session_id="s") == {**CATALOG["default"], "sessionId": "s"}
    prior = {"next": {"provider": "anthropic", "model": "claude-route", "reasoningEffort": "medium"}}
    assert _projected_model_selection(CATALOG, prior, session_id="s")["provider"] == "anthropic"


@pytest.mark.parametrize("provider,model", [("openai", "response-model"), ("openrouter", "vendor/model"), ("google", "gemini-route")])
def test_projection_explicit_route_does_not_force_deepseek_or_old_reasoning(provider, model):
    selected = _projected_model_selection(CATALOG, {}, session_id="s", provider=provider, model=model)
    assert selected == {"sessionId": "s", "provider": provider, "model": model}
    assert _projected_model_selection(CATALOG, {}, session_id="s", provider=provider, model=model, effort="high")["reasoningEffort"] == "high"


def test_model_only_change_keeps_existing_provider():
    selected = _projected_model_selection(CATALOG, {"lastUsed": {"provider": "custom", "model": "old", "reasoningEffort": "high"}}, session_id="s", model="new")
    assert selected == {"sessionId": "s", "provider": "custom", "model": "new"}


def test_provider_without_model_is_rejected_before_files_or_host(tmp_path):
    with pytest.raises(GuiWorkflowError, match="--model"):
        run_gui_agent_workflow(data_dir=tmp_path, prompt="test", output_dir=tmp_path / "out", provider="openai")
    assert not (tmp_path / "out").exists()
