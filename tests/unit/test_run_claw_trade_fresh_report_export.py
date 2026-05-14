import json

from scripts.run_claw_trade_fresh_report import export_worker_evidence


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_export_worker_evidence_uses_last_provider_request(tmp_path):
    call_dir = tmp_path / "call"
    output_dir = tmp_path / "out"
    call_dir.mkdir()
    output_dir.mkdir()

    initial_request = {
        "sequence": 1,
        "payload": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "initial instruction"}],
                }
            ]
        },
    }
    final_request = {
        "sequence": 2,
        "payload": {
            "messages": [
                {"role": "user", "content": "initial instruction"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "get_fundamentals",
                                "arguments": '{"ticker":"AAPL"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "content": "TOOL_RESULT: revenue evidence"},
            ]
        },
    }

    write_json(call_dir / "call.json", {"worker_id": "fundamental_analyst", "stage": "frontline"})
    write_json(
        call_dir / "openclaw-result.json",
        {
            "status": "succeeded",
            "provider_request_path": "provider-request.json",
            "raw_output_path": "raw-output.md",
        },
    )
    write_json(call_dir / "provider-request.json", initial_request)
    (call_dir / "provider-requests.jsonl").write_text(
        json.dumps(initial_request, ensure_ascii=False)
        + "\n"
        + json.dumps(final_request, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (call_dir / "raw-output.md").write_text("worker report", encoding="utf-8")

    export_worker_evidence(call_dir, output_dir)

    final_prompt = (output_dir / "fundamental_analyst_final_prompt.md").read_text(encoding="utf-8")
    initial_prompt = (output_dir / "fundamental_analyst_provider_prompt_initial.md").read_text(
        encoding="utf-8"
    )
    provider_final_prompt = (output_dir / "fundamental_analyst_provider_prompt_final.md").read_text(
        encoding="utf-8"
    )

    assert "TOOL_RESULT: revenue evidence" in final_prompt
    assert "### tool_calls" in final_prompt
    assert "`get_fundamentals`" in final_prompt
    assert "TOOL_RESULT: revenue evidence" not in initial_prompt
    assert provider_final_prompt == final_prompt
