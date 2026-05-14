import json
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from ..tools.flag_store import add_flag

console = Console()


def review_report(json_path: str):
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    formalized = data["formalized"]
    evidence = data["evidence"]

    console.print(Panel(
        f"Reviewing classifications for hypothesis:\n\n{formalized['core_hypothesis']}",
        title="[bold]Flag Review Mode[/bold]",
        border_style="cyan"
    ))
    console.print("[dim]For each paper: [k]eep classification / [f]lag as wrong / [q]uit[/dim]\n")

    flagged_count = 0
    total_reviewed = 0

    for cid, assessment in evidence["claim_evidence"].items():
        classifications = assessment.get("classifications", [])
        if not classifications:
            continue

        console.print(f"\n[bold cyan]Claim {cid}[/bold cyan]: "
                      f"{_get_claim_statement(formalized, cid)}\n")

        for cls in classifications:
            total_reviewed += 1
            console.print(Panel(
                f"[bold]{cls['paper_title']}[/bold]\n\n"
                f"Agent classified as: [yellow]{cls['classification']}[/yellow]\n"
                f"Justification quote: \"[italic]{cls.get('justification_quote', '')}[/italic]\"\n"
                f"Reasoning: {cls.get('reasoning', '')}",
                border_style="dim"
            ))

            while True:
                choice = input("[k]eep / [f]lag / [q]uit: ").strip().lower()
                if choice in ("k", ""):
                    break
                elif choice == "q":
                    console.print(f"\n[bold]Review complete.[/bold] "
                                  f"Flagged {flagged_count} of {total_reviewed} reviewed.\n")
                    return
                elif choice == "f":
                    _flag_paper(formalized, cls)
                    flagged_count += 1
                    break
                else:
                    console.print("[dim]Use k/f/q.[/dim]")

    console.print(f"\n[bold]Review complete.[/bold] "
                  f"Flagged {flagged_count} of {total_reviewed} reviewed.")
    console.print("[dim]Flags will be applied as few-shot examples in future runs.[/dim]\n")


def _get_claim_statement(formalized: dict, cid: str) -> str:
    for c in formalized.get("atomic_claims", []):
        if c["id"] == cid:
            return c["statement"]
    return "?"


def _flag_paper(formalized: dict, cls: dict):
    options = ["supports", "contradicts", "tangential", "confounder"]
    options = [o for o in options if o != cls["classification"]]

    console.print("\n[bold]Correct classification?[/bold]")
    for i, opt in enumerate(options, 1):
        console.print(f"  {i}. {opt}")

    while True:
        sel = input("Enter number (or Enter to skip): ").strip()
        if not sel:
            return
        try:
            correct = options[int(sel) - 1]
            break
        except (ValueError, IndexError):
            console.print("[dim]Invalid selection.[/dim]")

    reason = input("Brief reason (optional, Enter to skip): ").strip()

    add_flag(
        hypothesis_summary=formalized["core_hypothesis"],
        domain=formalized.get("domain", "unknown"),
        entities=formalized.get("key_entities", []) + formalized.get("starter_entities", []),
        paper_title=cls["paper_title"],
        paper_abstract_excerpt=cls.get("justification_quote", ""),
        agent_classification=cls["classification"],
        agent_justification=cls.get("reasoning", ""),
        user_classification=correct,
        user_reason=reason,
    )
    console.print("[green]Flagged.[/green]\n")
