"""One native date selector for preparation and map views."""
import tkinter as tk
from tkinter import ttk

from .datasets import DATASET_IDS, survey_date
from .viewer_preferences import STRINGS


class SurveySelector(ttk.Frame):
    def __init__(self, parent, selected, command, *, language="en", states=None):
        super().__init__(parent)
        self.command = command
        self.variable = tk.StringVar(value=selected)
        self.states = states or {}
        self.busy = False
        self.label = ttk.Label(self, style="Muted.TLabel")
        self.label.pack(side="left", padx=(0, 10))
        self.buttons = {}
        for id in DATASET_IDS:
            button = ttk.Radiobutton(self, value=id, variable=self.variable, command=self._selected,
                                     style="Survey.TRadiobutton", width=12, takefocus=True)
            button.pack(side="left", padx=(0, 3))
            button.bind("<Left>", lambda event, id=id: self._step(id, -1))
            button.bind("<Right>", lambda event, id=id: self._step(id, 1))
            button.bind("<Return>", lambda event, button=button: self._invoke(button))
            self.buttons[id] = button
        self.set_language(language)

    def set_language(self, language):
        self.language = language
        text = STRINGS[language]
        self.label.configure(text=text["survey"])
        for id, button in self.buttons.items():
            status = self.states.get(id)
            suffix = "\n" + text["survey_" + status] if status else ""
            button.configure(text=survey_date(id) + suffix)

    def set_states(self, states):
        self.states = states
        self.set_language(self.language)

    def set_busy(self, busy):
        self.busy = busy
        for button in self.buttons.values():
            button.state(["disabled"] if busy else ["!disabled"])

    def select(self, id):
        self.variable.set(id)

    def _selected(self):
        if not self.busy:
            self.command(self.variable.get())

    def _invoke(self, button):
        button.invoke()
        return "break"

    def _step(self, focused, direction):
        if not self.busy:
            index = (DATASET_IDS.index(focused) + direction) % len(DATASET_IDS)
            button = self.buttons[DATASET_IDS[index]]
            button.focus_set()
            button.invoke()
        return "break"

    def set_colors(self, colors):
        style = ttk.Style(self)
        style.layout("Survey.TRadiobutton", style.layout("TButton"))
        style.configure("Survey.TRadiobutton", padding=(8, 5), font=("Segoe UI", 10),
                        background=colors["surface"], foreground=colors["muted"],
                        borderwidth=1, bordercolor=colors["line"], focuscolor=colors["ink"])
        style.map("Survey.TRadiobutton",
                  background=[("disabled", colors["selection"]), ("selected", colors["accent"]),
                              ("active", colors["selection"])],
                  foreground=[("disabled", colors["disabled"]), ("selected", colors["on_accent"]),
                              ("active", colors["ink"])],
                  bordercolor=[("focus", colors["ink"]), ("selected", colors["accent"])])
