from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from ..tools.llm_client import TRACKER

console = Console()

VERDICT_COLORS = {
    "STRONG": "green",
    "MODERATE": "yellow",
    "WEAK": "dark_orange",
    "FALSIFIED": "red",
    "NOVEL-UNTESTED": "blue",
    "RESULTS-PROBLEMATIC": "magenta",
}

_CRITIQUE_SECTIONS = [
    ("methods_critique", "Methods critique"),
    ("statistical_critique", "Statistical rigor"),
    ("reproducibility_check", "Reproducibility check"),
    ("interpretation_critique", "Interpretation critique"),
]
_SEVERITY_COLOR = {"high": "red", "medium": "yellow", "low": "green"}


def _render_critique_panels(verdict: dict):
    blocks = []
    for key, label in _CRITIQUE_SECTIONS:
        c = verdict.get(key)
        if not c:
            continue
        sev = c.get("severity", "?")
        sev_color = _SEVERITY_COLOR.get(sev, "white")
        body = [f"[bold]Severity:[/bold] [{sev_color}]{sev}[/{sev_color}]"]
        for issue in c.get("issues", []):
            body.append(f"  • {issue}")
        if c.get("notes"):
            body.append(f"\n[dim]{c['notes']}[/dim]")
        blocks.append((label, "\n".join(body), sev_color))
    if not blocks:
        return
    console.print("\n[bold magenta]── COMPLETED-ANALYSIS CRITIQUE ──[/bold magenta]")
    for label, body, color in blocks:
        console.print(Panel(body, title=f"[bold]{label}[/bold]", border_style=color))


def render_report(formalized: dict, evidence: dict, verdict: dict, analyst_result: dict | None = None):
    console.print(Panel.fit(
        "[bold]HYPOTHESIS FALSIFICATION REPORT[/bold]",
        border_style="cyan"
    ))

    console.print(Panel(
        formalized["core_hypothesis"],
        title="[bold]Core Hypothesis[/bold]",
        border_style="cyan"
    ))

    flags_note = (f"\n[bold]Past flags applied:[/bold] {evidence.get('flags_applied', 0)}"
                  if evidence.get("flags_applied") else "")
    console.print(Panel(
        f"[bold]Domain:[/bold] {formalized.get('domain', 'unknown')}\n"
        f"[bold]Cited literature:[/bold] {len(formalized.get('cited_literature', []))} refs\n"
        f"[bold]Proposed methods:[/bold] {len(formalized.get('proposed_methods', []))} steps\n"
        f"[bold]Starter entities:[/bold] {', '.join(formalized.get('starter_entities', [])[:10])}"
        f"{flags_note}",
        title="[bold]Input Context[/bold]",
        border_style="dim"
    ))

    api_table = Table(title="Literature Source Status", box=box.SIMPLE)
    api_table.add_column("Source")
    api_table.add_column("Status")
    for src, statuses in evidence.get("api_status", {}).items():
        ok_count = sum(1 for s in statuses if s.startswith("ok"))
        disabled_count = sum(1 for s in statuses if s.startswith("disabled"))
        fail_count = len(statuses) - ok_count - disabled_count
        bad = fail_count + disabled_count
        color = "green" if bad == 0 else "yellow" if ok_count > 0 else "red"
        parts = [f"{ok_count} ok"]
        if fail_count:
            parts.append(f"{fail_count} failed")
        if disabled_count:
            parts.append(f"{disabled_count} skipped (host down)")
        api_table.add_row(src, f"[{color}]{' / '.join(parts)}[/{color}]")
    console.print(api_table)

    claims_table = Table(title="Atomic Claims", box=box.ROUNDED)
    claims_table.add_column("ID", style="cyan")
    claims_table.add_column("Claim")
    claims_table.add_column("Null H₀", style="dim")
    for c in formalized.get("atomic_claims", []):
        claims_table.add_row(c["id"], c["statement"], c["null_hypothesis"])
    console.print(claims_table)

    ev_table = Table(title="Evidence Summary (by claim)", box=box.ROUNDED)
    ev_table.add_column("Claim")
    ev_table.add_column("Supp", style="green")
    ev_table.add_column("Contra", style="red")
    ev_table.add_column("Tang", style="dim")
    ev_table.add_column("Conf", style="yellow")
    ev_table.add_column("Strength")
    ev_table.add_column("Novelty")
    for cid, a in evidence.get("claim_evidence", {}).items():
        classifications = a.get("classifications", [])
        supp = sum(1 for c in classifications if c["classification"] == "supports")
        contra = sum(1 for c in classifications if c["classification"] == "contradicts")
        tang = sum(1 for c in classifications if c["classification"] == "tangential")
        conf = sum(1 for c in classifications if c["classification"] == "confounder")
        ev_table.add_row(cid, str(supp), str(contra), str(tang), str(conf),
                         a.get("evidence_strength", "?"), a.get("novelty_flag", "?"))
    console.print(ev_table)

    console.print("\n[bold]Alternative Explanations:[/bold]")
    for i, alt in enumerate(verdict.get("top_alternative_explanations", []), 1):
        plaus_color = {"high": "red", "medium": "yellow", "low": "green"}.get(
            alt.get("plausibility", ""), "white"
        )
        console.print(f"  {i}. [{plaus_color}]({alt.get('plausibility', '?')})[/{plaus_color}] {alt['explanation']}")
        console.print(f"     [dim]→ {alt.get('why', '')}[/dim]")
        console.print(f"     [dim]Rule out by: {alt.get('how_to_rule_out', '')}[/dim]\n")

    score_table = Table(title="Stress Test Scorecard", box=box.ROUNDED)
    score_table.add_column("Category")
    score_table.add_column("Score")
    score_table.add_column("Visual", width=22)
    for cat, score in verdict.get("scores", {}).items():
        score = int(score)
        bar = "█" * score + "░" * (10 - score)
        color = "red" if score <= 3 else "yellow" if score <= 6 else "green"
        score_table.add_row(
            cat.replace("_", " ").title(),
            f"{score}/10",
            f"[{color}]{bar}[/{color}]"
        )
    console.print(score_table)

    v = verdict.get("verdict", "UNKNOWN")
    console.print(Panel(
        f"[bold]{v}[/bold]\n\n{verdict.get('verdict_justification', '')}",
        title="Verdict",
        border_style=VERDICT_COLORS.get(v, "white")
    ))

    console.print(Panel(
        verdict.get("decisive_experiment", ""),
        title="[bold]Decisive Experiment[/bold]",
        border_style="cyan"
    ))

    _render_critique_panels(verdict)

    if analyst_result and not analyst_result.get("skipped"):
        interp = analyst_result.get("interpretation", {})
        set_a_stats = analyst_result.get("set_a_stats") or {}
        set_b_stats = analyst_result.get("set_b_stats") or {}
        cross_set = analyst_result.get("cross_set") or {}
        set_a = analyst_result.get("set_a", [])
        set_b = analyst_result.get("set_b", [])

        stats_lines = []
        if set_a_stats.get("valid_gene_count"):
            dnds = set_a_stats.get("dnds_mean")
            stats_lines.append(
                f"[bold]Set A[/bold] ({', '.join(set_a[:5])}{'...' if len(set_a) > 5 else ''}): "
                f"mean dN/dS={f'{dnds:.3f}' if dnds is not None else 'n/a'}, "
                f"orthologs={set_a_stats.get('mean_ortholog_count', 0):.1f} avg"
            )
        if set_b_stats.get("valid_gene_count"):
            dnds = set_b_stats.get("dnds_mean")
            stats_lines.append(
                f"[bold]Set B[/bold] ({', '.join(set_b[:5])}{'...' if len(set_b) > 5 else ''}): "
                f"mean dN/dS={f'{dnds:.3f}' if dnds is not None else 'n/a'}, "
                f"orthologs={set_b_stats.get('mean_ortholog_count', 0):.1f} avg"
            )
        if cross_set:
            j = cross_set.get("jaccard_index", 0)
            stats_lines.append(f"[bold]Regulatory overlap (Jaccard):[/bold] {j:.3f}")
            shared = cross_set.get("shared_tfs", [])
            if shared:
                stats_lines.append(f"Shared TF motifs: {', '.join(shared[:5])}")

        assessment = interp.get("overall_genomic_assessment", "inconclusive")
        assessment_color = {
            "supports": "green", "contradicts": "red", "inconclusive": "yellow"
        }.get(assessment, "white")

        body = "\n".join([
            f"[bold]Overall:[/bold] [{assessment_color}]{assessment}[/{assessment_color}]",
            interp.get("assessment_justification", ""),
            "",
            *stats_lines,
        ]).strip()

        for p in interp.get("patterns_observed", []):
            sup = {"yes": "[green]✓[/green]", "no": "[red]✗[/red]", "neutral": "[dim]~[/dim]"}.get(
                p.get("supports_hypothesis", "neutral"), "[dim]~[/dim]"
            )
            body += f"\n  {sup} {p['pattern']}  [dim]({p.get('evidence', '')})[/dim]"

        console.print(Panel(body, title="[bold]Genomic Evidence (Analyst)[/bold]", border_style="cyan"))

    if verdict.get("librarian_sanity_check"):
        console.print(Panel(
            verdict["librarian_sanity_check"],
            title="[bold dim]Skeptic's Sanity-Check of Librarian[/bold dim]",
            border_style="dim"
        ))

    console.print(
        f"\n[dim]Claude: {TRACKER.claude_input:,} in / {TRACKER.claude_output:,} out "
        f"across {TRACKER.calls_claude} calls • "
        f"Local: {TRACKER.local_input:,} in / {TRACKER.local_output:,} out "
        f"across {TRACKER.calls_local} calls • "
        f"est. Claude cost: ${TRACKER.cost_estimate():.3f}[/dim]"
    )
    console.print("[dim]Run `python -m nullifier.cli review <json-file>` to flag misclassifications.[/dim]\n")
