import argparse
import json
import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't encode characters Rich emits
# (box drawing, ₀ subscripts in dN/dS values, ▶/✓ status glyphs). Force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def cmd_run(args):
    from rich.console import Console
    from .pipeline import run_pipeline
    from .report.renderer import render_report

    raw = Path(args.input).read_text(encoding="utf-8")
    if len(raw) < 50:
        print("Error: input file too short.", file=sys.stderr)
        sys.exit(1)

    console = Console(stderr=True)
    debug = getattr(args, "debug", False)

    confirm_cb = None if args.no_confirm else _build_confirm_callback(console)

    formalized = evidence = verdict = analyst = None

    for event in run_pipeline(raw, confirm_callback=confirm_cb, max_papers=args.max_papers):
        if debug:
            console.print(f"[dim][{event.type}] {event.payload}[/dim]")
        _handle_event(event, console)

        if event.type == "run_completed":
            formalized = event.payload["formalized"]
            evidence = event.payload["evidence"]
            verdict = event.payload["verdict"]
            analyst = event.payload["analyst"]
        elif event.type == "run_aborted":
            sys.exit(0)
        elif event.type == "run_failed":
            console.print(f"[red]Pipeline failed: {event.payload['error']}[/red]", err=True)
            sys.exit(1)

    if formalized and evidence and verdict:
        render_report(formalized, evidence, verdict, analyst_result=analyst)

        if args.output_json:
            out: dict = {"formalized": formalized, "evidence": evidence, "verdict": verdict}
            if analyst:
                out["analyst"] = analyst
            Path(args.output_json).write_text(
                json.dumps(out, indent=2, default=str), encoding="utf-8"
            )
            console.print(f"\nReport saved to {args.output_json}")


def _handle_event(event, console) -> None:
    t = event.type
    p = event.payload
    if t == "stage_started":
        console.print(f"[bold cyan]▶ {p['label']}...[/bold cyan]")
    elif t == "hypothesis_extracted":
        domain_color = "green" if p["is_biology"] else "yellow"
        console.print(
            f"  Domain: [{domain_color}]{p['domain']}[/{domain_color}] | "
            f"Key entities: {len(p['key_entities'])} | "
            f"Starter entities: {len(p['starter_entities'])}"
        )
    elif t == "claims_formalized":
        console.print(f"  [bold]{p['claim_count']} atomic claim(s) identified[/bold]")
    elif t == "synthesis_ready":
        strength_color = {"strong": "green", "moderate": "yellow", "weak": "orange1", "absent": "red"}.get(
            p["evidence_strength"], "white"
        )
        console.print(
            f"  [{p['claim_id']}] strength=[{strength_color}]{p['evidence_strength']}[/{strength_color}]"
            f", novelty={p['novelty_flag']}"
        )
    elif t == "formalizer_detected_completed_analysis":
        console.print(f"  [yellow]Detected {p['finding_count']} completed-analysis finding(s) — critique mode will activate[/yellow]")
    elif t == "analyst_started":
        console.print(f"  Fetching Ensembl data for {p['gene_count']} gene(s)...")
    elif t == "analyst_reproducibility_check_start":
        console.print(f"  Cross-referencing {p['finding_count']} reported finding(s) against Ensembl...")
    elif t == "analyst_reproducibility_check_complete":
        console.print(f"  Reproducibility: {p['verifiable_count']}/{p['total']} finding(s) checkable here")
    elif t == "skeptic_critique_mode_active":
        console.print(f"  [bold yellow]Critique mode: evaluating {p['finding_count']} reported result(s)[/bold yellow]")
    elif t == "analyst_ready":
        assess_color = {"supports": "green", "contradicts": "red"}.get(
            p["overall_genomic_assessment"], "yellow"
        )
        console.print(
            f"  Genomic assessment: [{assess_color}]{p['overall_genomic_assessment']}[/{assess_color}]"
        )
    elif t == "analyst_skipped":
        console.print(f"  [dim]Analyst skipped ({p['reason']})[/dim]")
    elif t == "stage_completed":
        console.print(f"  [dim green]✓ done[/dim green]")
    elif t == "verdict_ready":
        v = p["verdict"]
        color = {"STRONG": "green", "MODERATE": "yellow", "NOVEL-UNTESTED": "cyan",
                 "RESULTS-PROBLEMATIC": "magenta"}.get(v, "red")
        score = p.get("scores", {}).get("overall_falsifiability_score", "?")
        console.print(
            f"\n  Verdict: [bold {color}]{v}[/bold {color}]  "
            f"[dim](overall score: {score}/10)[/dim]"
        )


def _read_multiline(prompt: str) -> list[str]:
    print(prompt)
    lines = []
    while True:
        line = input()
        if not line:
            break
        lines.append(line)
    return lines


def _build_confirm_callback(console):
    from . import events as ev

    def _render_value(kind: str, value) -> str:
        if kind == "text":
            return str(value or "")
        if kind == "list":
            return "\n".join(f"  - {v}" for v in (value or [])) or "  (none)"
        if kind == "findings":
            if not value:
                return "  (none)"
            return "\n".join(
                f"  - {f.get('finding', '')}"
                f"{' | ' + f.get('statistic', '') if f.get('statistic') else ''}"
                f"{' | ' + f.get('test', '') if f.get('test') else ''}"
                f"{' | ' + f.get('sample_size', '') if f.get('sample_size') else ''}"
                f"{chr(10) + '    → ' + f.get('interpretation', '') if f.get('interpretation') else ''}"
                for f in value
            )
        return str(value)

    def _gate(stage1: dict) -> dict | None:
        sections = ev.build_confirm_sections(stage1)
        console.print("\n" + "=" * 70)
        console.print(f"[bold]EXTRACTED STRUCTURE[/bold]  (domain: [cyan]{stage1.get('domain', 'unknown')}[/cyan])")
        console.print("=" * 70)
        edits: dict = {}
        for s in sections:
            tag = " [yellow](detected)[/yellow]" if s["detected"] else ""
            console.print(f"\n[bold cyan]{s['label']}[/bold cyan]{tag}")
            console.print(_render_value(s["kind"], s["value"]))
            options = ["keep", "edit"]
            if s["removable"]:
                options.append("remove")
            while True:
                console.print("  Actions:")
                for i, opt in enumerate(options, 1):
                    console.print(f"    {i}. {opt}")
                choice = input("  Choose 1-3 or type the action: ").strip().lower()
                if choice in ("", "1", "keep"):
                    break
                if choice in ("2", "edit"):
                    if s["kind"] == "text":
                        lines = _read_multiline("  Enter new text (blank line to end):")
                        if lines:
                            edits[s["id"]] = {"action": "edit", "value": " ".join(lines)}
                    elif s["kind"] == "list":
                        lines = _read_multiline("  Enter one item per line (blank line to end):")
                        edits[s["id"]] = {"action": "edit", "value": lines}
                    else:  # findings — line format: finding | statistic | test | sample_size | interpretation
                        lines = _read_multiline(
                            "  One finding per line, pipe-separated "
                            "(finding | statistic | test | sample_size | interpretation):"
                        )
                        findings = []
                        for ln in lines:
                            parts = [p.strip() for p in ln.split("|")]
                            parts += [""] * (5 - len(parts))
                            findings.append({
                                "finding": parts[0], "statistic": parts[1], "test": parts[2],
                                "sample_size": parts[3], "interpretation": parts[4],
                            })
                            edits[s["id"]] = {"action": "edit", "value": findings}
                    break
                if s["removable"] and choice in ("3", "remove"):
                    edits[s["id"]] = {"action": "remove"}
                    break
                console.print("[dim]Choose one of the listed actions.[/dim]")

        final = input("\nProceed? yes / abort: ").strip().lower()
        if final in ("abort", "no"):
            console.print("Aborted.", style="red")
            return None
        updated, changed = ev.apply_section_edits(stage1, edits)
        if changed:
            console.print(f"[dim]Applied edits to: {', '.join(changed)}[/dim]")
        return updated

    return _gate


def cmd_serve(args):
    import uvicorn
    from .tools.llm_client import health_check_local
    from .tools.r_bridge import initialize_r

    ok, msg = health_check_local()
    status = "OK" if ok else "!!"
    print(f"[{status}] LM Studio: {msg}")
    if not ok:
        print("    Local-model tasks will fail until LM Studio is running.")

    r_health = initialize_r()
    r_status = "OK" if r_health.ok else "!!"
    print(f"[{r_status}] R/PAML: {r_health.message}")
    if r_health.enabled and not r_health.ok:
        raise SystemExit("R/PAML health check failed. Install the missing dependencies or set [r].enabled=false.")

    print(f"\nStarting Nullifier server at http://{args.host}:{args.port}")
    uvicorn.run(
        "nullifier.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def cmd_review(args):
    from .review.interactive import review_report
    review_report(args.json_file)


def cmd_flags_list(args):
    from .tools.flag_store import list_all_flags
    flags = list_all_flags()
    if not flags:
        print("No flags recorded yet.")
        return
    for f in flags:
        print(f"[{f['created_at'][:19]}] {f['paper_title'][:80]}")
        print(f"  Agent: {f['agent_classification']} → Corrected: {f['user_classification']}")
        if f["user_reason"]:
            print(f"  Reason: {f['user_reason']}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Scientific Hypothesis Stress-Tester")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run the full pipeline on a hypothesis file")
    p_run.add_argument("--input", required=True, help="Path to hypothesis text file")
    p_run.add_argument("--max-papers", type=int, default=6,
                       help="Max papers retrieved per atomic claim (default: 6)")
    p_run.add_argument("--output-json", default=None,
                       help="Save full report JSON to this path")
    p_run.add_argument("--no-confirm", action="store_true",
                       help="Skip the hypothesis confirmation gate")
    p_run.add_argument("--debug", action="store_true",
                       help="Print raw event stream alongside normal output")
    p_run.set_defaults(func=cmd_run)

    p_review = sub.add_parser("review",
                               help="Interactively review classifications and flag errors")
    p_review.add_argument("json_file", help="Path to JSON report from a previous run")
    p_review.set_defaults(func=cmd_review)

    p_serve = sub.add_parser("serve", help="Start the FastAPI + WebSocket server")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    p_serve.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")
    p_serve.set_defaults(func=cmd_serve)

    p_flags = sub.add_parser("flags", help="Manage flag database")
    p_flags_sub = p_flags.add_subparsers(dest="flag_cmd", required=True)
    p_flags_list = p_flags_sub.add_parser("list", help="List all recorded flags")
    p_flags_list.set_defaults(func=cmd_flags_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
