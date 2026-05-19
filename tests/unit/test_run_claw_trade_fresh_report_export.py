import json

from scripts.run_claw_trade_fresh_report import export_run_evidence, export_worker_evidence


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
                                "name": "claw_get_fundamental_pack",
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
    assert "`claw_get_fundamental_pack`" in final_prompt
    assert "TOOL_RESULT: revenue evidence" not in initial_prompt
    assert provider_final_prompt == final_prompt


def test_export_run_evidence_keeps_duplicate_worker_turns_separate(tmp_path):
    run_dir = tmp_path / "runs" / "run-1"
    output_dir = tmp_path / "out"
    calls_dir = run_dir / "calls"
    reports_dir = run_dir / "reports"
    calls_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    (reports_dir / "final-report.md").write_text("# Final\n", encoding="utf-8")

    for turn_index, body in ((0, "part one"), (1, "part two"), (2, "part three")):
        call_dir = calls_dir / f"call-{turn_index}"
        call_dir.mkdir()
        write_json(
            call_dir / "call.json",
            {
                "worker_id": "report_polisher",
                "stage": "final_report",
                "turn_index": turn_index,
            },
        )
        write_json(
            call_dir / "openclaw-result.json",
            {
                "status": "succeeded",
                "provider_request_path": "provider-request.json",
                "raw_output_path": "raw-output.md",
            },
        )
        write_json(
            call_dir / "provider-request.json",
            {"payload": {"messages": [{"role": "user", "content": f"instruction {turn_index}"}]}},
        )
        (call_dir / "raw-output.md").write_text(body, encoding="utf-8")

    summary = export_run_evidence(run_dir, output_dir)

    paths = [item["report_path"] for item in summary["workers"]]
    assert paths == [
        "report_polisher_t00_report.md",
        "report_polisher_t01_report.md",
        "report_polisher_t02_report.md",
    ]
    assert (output_dir / "report_polisher_t00_report.md").read_text(encoding="utf-8") == "part one"
    assert (output_dir / "report_polisher_t01_report.md").read_text(encoding="utf-8") == "part two"
    assert (output_dir / "report_polisher_t02_report.md").read_text(encoding="utf-8") == "part three"
