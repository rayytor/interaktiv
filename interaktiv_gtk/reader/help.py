"""
The help sheet: what a finger does, then what a keyboard does.

Touch comes first because a board has no keyboard; the shortcuts are for the
same book opened on a teacher's laptop.
"""

from gi.repository import Adw, Gtk

TOUCH = [
    ("Sayfa çevir", "Ekranın sağ ya da sol kenarına dokunun veya sayfayı yana kaydırın."),
    ("Hızlı ilerle", "Kenara basılı tutun."),
    ("Yakınlaştır", "Sayfaya iki kez dokunun veya iki parmağınızı açın."),
    ("Etkinliğe odaklan", "Çerçeveli etkinliğe dokunun."),
    ("Sonraki etkinlik", "Odaktayken yana kaydırın."),
    ("Odaktan çık", "Etkinliğin dışındaki boş alana dokunun."),
    ("Düğmenin adını gör", "Düğmeye basılı tutun."),
]

KEYBOARD = [
    ("Gezinme", [
        ("Sonraki sayfa", "→"),
        ("Önceki sayfa", "←"),
        ("İlk sayfa", "Home"),
        ("Son sayfa", "End"),
    ]),
    ("Görünüm", [
        ("Yakınlaştır / uzaklaştır", "+  −"),
        ("Sayfaya sığdır", "0"),
        ("Çift sayfa / tek sayfa / kaydırma", "B  S  C"),
        ("Döndür", "R"),
        ("Tam ekran", "F"),
        ("Okuma teması", "M"),
    ]),
    ("Araçlar", [
        ("Belgede ara", "Ctrl + F"),
        ("Kenar çubuğu", "T"),
        ("Etkinlikleri göster / gizle", "A"),
        ("Yardım", "?"),
    ]),
    ("Odak modu", [
        ("Sonraki / önceki etkinlik", "→  ←"),
        ("Sonraki / önceki soru", "↓  ↑"),
        ("Odaktan çık", "Esc"),
    ]),
]


class HelpWindow(Adw.Window):
    __gtype_name__ = "InteraktivHelpWindow"

    def __init__(self, parent=None):
        super().__init__(
            title="Yardım",
            modal=True,
            transient_for=parent if isinstance(parent, Gtk.Window) else None,
            default_width=640,
            default_height=720,
        )
        self.add_css_class("help-window")

        page = Adw.PreferencesPage()
        page.add(self._touch_group())
        for title, rows in KEYBOARD:
            page.add(self._keyboard_group(title, rows))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Adw.HeaderBar())
        page.set_vexpand(True)
        box.append(page)
        self.set_content(box)

        close = Gtk.ShortcutController()
        close.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
            action=Gtk.CallbackAction.new(lambda *_: (self.close(), True)[1]),
        ))
        self.add_controller(close)

    @staticmethod
    def _touch_group() -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Dokunarak")
        for title, how in TOUCH:
            group.add(Adw.ActionRow(title=title, subtitle=how))
        return group

    @staticmethod
    def _keyboard_group(title: str, rows) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=f"Klavye · {title}")
        for label, keys in rows:
            row = Adw.ActionRow(title=label)
            key = Gtk.Label(label=keys, valign=Gtk.Align.CENTER)
            key.add_css_class("keycap")
            row.add_suffix(key)
            group.add(row)
        return group
