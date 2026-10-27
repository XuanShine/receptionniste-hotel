"""Tableau de bord de la réception."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from nicegui import ui

import desk

_REFRESH_SECONDS = 5.0


def main() -> None:
    @ui.page("/")
    def reception() -> None:
        today = date.today().isoformat()
        state = {
            "start": today,
            "end": today,
            "selected": None,
            "saved": "",
            "seen": None,
            "hold": False,
            "view": "appels",
        }

        ui.query("body").style("background: #f3efe6; color: #1c1915;")
        with ui.column().classes("w-full max-w-6xl mx-auto p-6 gap-5"):
            with ui.row().classes("w-full items-end justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label("Hôtel Panorama").classes("text-3xl font-medium")
                    ui.label("Réception").classes("text-stone-600")
                with ui.row().classes("items-end gap-3"):
                    start_input = ui.input("Début", value=state["start"]).props("type=date").classes("w-44")
                    end_input = ui.input("Fin", value=state["end"]).props("type=date").classes("w-44")
            stats = ui.row().classes("w-full gap-3 flex-wrap")
            with ui.row().classes("w-full items-start gap-4 flex-wrap"):
                with ui.column().classes("w-full md:w-96 gap-3"):
                    with ui.row().classes("gap-2"):
                        appels_tab = ui.button("Appels", on_click=lambda: switch("appels")).props(
                            "no-caps color=primary"
                        )
                        trash_tab = ui.button("Poubelle", on_click=lambda: switch("poubelle")).props(
                            "no-caps outline color=primary"
                        )
                    call_list = ui.column().classes("w-full gap-2")
                    with ui.column().classes("w-full gap-2") as message_block:
                        ui.label("Messages").classes("text-xl")
                        message_list = ui.column().classes("w-full gap-2")
                with ui.column().classes("min-w-72 flex-1 gap-3"):
                    heading = ui.label("Choisissez un appel ou un message.").classes("text-lg")
                    editor = ui.textarea().classes("w-full font-mono").props("rows=22")
                    with ui.row().classes("gap-2"):
                        ui.button("Enregistrer", on_click=lambda: save()).props("color=primary")
                        ui.button("Annuler", on_click=lambda: cancel()).props("outline")
                        delete_button = ui.button("Supprime", on_click=lambda: act_on_selected(suppress)).props(
                            "outline no-caps color=negative"
                        )
                        restore_button = ui.button("Restaurer", on_click=lambda: act_on_selected(revive)).props(
                            "outline no-caps"
                        )
                    notice = ui.label("").classes("text-sm text-stone-600")

        def dirty() -> bool:
            return editor.value != state["saved"]

        def open_path(path: str) -> None:
            if dirty():
                notice.set_text("Enregistrez ou annulez avant de changer de document.")
                return
            try:
                text = desk.read_document(Path(path))
            except OSError:
                notice.set_text("Le fichier est illisible.")
                return
            state["selected"] = path
            state["saved"] = text
            editor.value = text
            notice.set_text("")
            state["seen"] = None
            draw()

        def save() -> None:
            selected = state["selected"]
            if not isinstance(selected, str):
                return
            text = editor.value or ""
            try:
                desk.save_document(Path(selected), text)
            except OSError:
                notice.set_text("Enregistrement impossible.")
                return
            state["saved"] = text
            notice.set_text("Enregistré.")
            state["seen"] = None
            draw()

        def cancel() -> None:
            editor.value = state["saved"] if isinstance(state["saved"], str) else ""
            notice.set_text("")

        def switch(view: str) -> None:
            if view not in ("appels", "poubelle") or state["view"] == view:
                return
            state["view"] = view
            state["seen"] = None
            draw()

        def act_on_selected(action) -> None:
            selected = state["selected"]
            if isinstance(selected, str):
                action(selected)

        def suppress(path: str) -> None:
            _retag(
                path,
                desk.soft_delete,
                "Enregistrez ou annulez avant de supprimer l'appel.",
                "Appel mis à la poubelle.",
                "Suppression impossible.",
            )

        def revive(path: str) -> None:
            _retag(
                path,
                desk.restore_call,
                "Enregistrez ou annulez avant de restaurer l'appel.",
                "Appel restauré.",
                "Restauration impossible.",
            )

        def _retag(path: str, rewrite, blocked: str, done: str, failed: str) -> None:
            if state["selected"] == path and dirty():
                notice.set_text(blocked)
                return
            try:
                text = rewrite(Path(path))
            except OSError:
                notice.set_text(failed)
                return
            if state["selected"] == path:
                state["saved"] = text
                editor.value = text
            notice.set_text(done)
            state["seen"] = None
            draw()

        def draw() -> None:
            span = desk.Span.of(_day(state["start"]), _day(state["end"]))
            board = desk.load_day(span)
            paths = _paths(board)
            selected = state["selected"] if isinstance(state["selected"], str) else None
            if selected is not None and selected not in paths and not dirty():
                state["selected"] = None
                state["saved"] = ""
                editor.value = ""
                selected = None
            view = state["view"] if state["view"] in ("appels", "poubelle") else "appels"
            seen = (span.start, span.end, selected, view, _signature(board))
            if seen == state["seen"]:
                return
            state["seen"] = seen
            _counts(stats, desk.DayDesk(board.span, board.active_calls, board.messages))
            calls = listed_calls(board, view)
            empty = (
                "Aucun appel dans la poubelle."
                if view == "poubelle"
                else "Aucun appel sur cette période."
            )
            _calls(
                call_list,
                calls,
                selected,
                open_path,
                empty,
                suppress if view == "appels" else None,
                revive if view == "poubelle" else None,
            )
            message_block.set_visibility(view == "appels")
            if view == "appels":
                _messages(message_list, board, selected, open_path)
            _mark_tab(appels_tab, view == "appels")
            _mark_tab(trash_tab, view == "poubelle")
            _mark_call_actions(delete_button, restore_button, board, selected)
            heading.set_text(_heading(board, selected))

        def on_period(_event) -> None:
            if state["hold"]:
                return
            if dirty():
                state["hold"] = True
                start_input.value = state["start"]
                end_input.value = state["end"]
                state["hold"] = False
                notice.set_text("Enregistrez ou annulez avant de changer de période.")
                return
            state["start"] = _day(start_input.value).isoformat()
            state["end"] = _day(end_input.value).isoformat()
            draw()

        start_input.on_value_change(on_period)
        end_input.on_value_change(on_period)
        draw()
        ui.timer(_REFRESH_SECONDS, draw)

    ui.run(
        host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
        port=int(os.environ.get("DASHBOARD_PORT", "8080")),
        title="Réception",
        language="fr",
        reload=False,
        show=False,
        dark=False,
    )


def _signature(board: desk.DayDesk) -> tuple[object, ...]:
    calls = tuple((str(call.path), call.resume, call.transcript) for call in board.calls)
    notes = tuple((str(note.path), note.name, note.phone, note.reason) for note in board.messages)
    return calls, notes


def _paths(board: desk.DayDesk) -> set[str]:
    return {str(call.path) for call in board.calls} | {str(note.path) for note in board.messages}


def _day(value: object) -> date:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return date.today()
    return date.today()


def _heading(board: desk.DayDesk, selected: str | None) -> str:
    if selected is None:
        return "Choisissez un appel ou un message."
    for call in board.calls:
        if str(call.path) == selected:
            who = "Console" if call.console else call.caller
            return f"Appel {call.started:%d/%m %H:%M} · {who}"
    for note in board.messages:
        if str(note.path) == selected:
            return f"Message {note.when:%d/%m %H:%M} · {note.name}"
    return "Choisissez un appel ou un message."


def _counts(host, board: desk.DayDesk) -> None:
    host.clear()
    figures = (
        ("Appels", len(board.calls)),
        ("Réservations", board.reservation_count),
        ("Transferts", board.transfer_count),
        ("Messages", len(board.messages)),
        ("Sans résumé", board.missing_resume_count),
    )
    with host:
        for label, count in figures:
            with ui.card().classes("bg-white px-4 py-3"):
                ui.label(str(count)).classes("text-2xl")
                ui.label(label).classes("text-stone-600 text-sm")


def listed_calls(board: desk.DayDesk, view: str) -> tuple[desk.CallCard, ...]:
    if view == "poubelle":
        return board.deleted_calls
    return board.active_calls


def _calls(host, calls, selected: str | None, show, empty: str, on_delete, on_restore) -> None:
    host.clear()
    with host:
        if not calls:
            ui.label(empty).classes("text-stone-600")
            return
        for call in calls:
            path = str(call.path)
            border = "border-2 border-stone-800" if path == selected else "border border-stone-300"
            row = ui.card().classes(f"w-full bg-white {border}")
            with row:
                with ui.row().classes("w-full items-start flex-nowrap gap-2"):
                    with ui.column().classes("min-w-0 flex-1 cursor-pointer gap-1") as opener:
                        with ui.row().classes("w-full items-baseline justify-between"):
                            ui.label(call.started.strftime("%d/%m %H:%M")).classes("font-medium")
                            ui.label(call.caller).classes("text-sm")
                        if call.console:
                            ui.badge("console", color="grey")
                        ui.label(call.resume or "Sans résumé").classes("text-sm text-stone-700")
                        with ui.row().classes("gap-1 flex-wrap"):
                            for label, color in _chips(call):
                                ui.badge(label, color=color)
                    opener.on("click", lambda _event, item=path: show(item))
                    if on_delete is not None:
                        ui.button("Supprime", on_click=lambda _event, item=path: on_delete(item)).props(
                            "flat dense no-caps color=negative"
                        )
                    if on_restore is not None:
                        ui.button("Restaurer", on_click=lambda _event, item=path: on_restore(item)).props(
                            "flat dense no-caps"
                        )


def _messages(host, board: desk.DayDesk, selected: str | None, show) -> None:
    host.clear()
    with host:
        if not board.messages:
            ui.label("Aucun message sur cette période.").classes("text-stone-600")
            return
        for note in board.messages:
            path = str(note.path)
            border = "border-2 border-stone-800" if path == selected else "border border-stone-300"
            row = ui.card().classes(f"w-full cursor-pointer bg-white {border}")
            row.on("click", lambda _event, item=path: show(item))
            with row:
                with ui.row().classes("w-full items-baseline justify-between"):
                    ui.label(note.name).classes("font-medium")
                    ui.label(note.when.strftime("%d/%m %H:%M")).classes("text-sm text-stone-600")
                ui.label(note.phone)
                ui.label(note.reason).classes("text-stone-700")


def _chips(call: desk.CallCard) -> list[tuple[str, str]]:
    chips: list[tuple[str, str]] = []
    seen: set[str] = set()
    for issue in call.issues:
        label, color = _chip(issue)
        if label in seen:
            continue
        seen.add(label)
        chips.append((label, color))
    if call.resume is None:
        chips.append(("sans résumé", "grey"))
    for label in desk.visible_tags(call):
        if label in seen:
            continue
        seen.add(label)
        chips.append((label, "primary"))
    return chips


def _mark_tab(button, active: bool) -> None:
    if active:
        button.props(remove="outline")
        button.props("color=primary no-caps")
        return
    button.props("outline color=primary no-caps")


def _mark_call_actions(delete_button, restore_button, board: desk.DayDesk, selected: str | None) -> None:
    call = next((item for item in board.calls if str(item.path) == selected), None) if selected else None
    if call is None:
        delete_button.set_visibility(False)
        restore_button.set_visibility(False)
        return
    deleted = desk.is_deleted(call)
    delete_button.set_visibility(not deleted)
    restore_button.set_visibility(deleted)


def _chip(issue: desk.Issue) -> tuple[str, str]:
    if isinstance(issue, desk.Reservation):
        return "réservation", "positive"
    if isinstance(issue, desk.Transfer):
        return ("transfert incomplet", "warning") if issue.unfinished else ("transfert", "info")
    if isinstance(issue, desk.Callback):
        return "message", "accent"
    if isinstance(issue, desk.Quote):
        return "devis", "secondary"
    return "échec", "negative"


if __name__ == "__main__":
    main()
