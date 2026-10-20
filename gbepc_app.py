# -*- coding: utf-8 -*-
"""
G-BEPC - bibliothèque de PDF pour élèves   |   @By/G-SOCIETY/2026
(lancé par main.py, qui affiche les erreurs à l'écran)

Dépendances : kivy, kivymd==1.1.1 (ou 1.2.0), pymupdf
Lancement   : python main.py   (my.kv est chargé automatiquement par MyApp)
"""
import hashlib
import json
import os
import queue
import shutil
import sys
import threading
import time
import traceback
import unicodedata
import uuid
import webbrowser
from datetime import datetime

from kivy.animation import Animation
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.core.clipboard import Clipboard
from kivy.core.image import Image as CoreImage
from kivy.metrics import dp
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.resources import resource_add_path
from kivy.uix.screenmanager import FadeTransition, SlideTransition
from kivy.utils import platform

from kivymd.app import MDApp
from kivymd.toast import toast
from kivymd.uix.button import MDFlatButton
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.pickers import MDTimePicker
from kivymd.uix.screen import MDScreen

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None


class PdfLocked(Exception):
    """PDF protégé par mot de passe."""


class FitzDoc:
    """Lecture PDF sur ordinateur (PyMuPDF)."""

    def __init__(self, path):
        self._d = fitz.open(path)
        if self._d.needs_pass:
            self._d.close()
            raise PdfLocked()
        self.page_count = self._d.page_count

    def render_file(self, n, width, out):
        page = self._d[n]
        zoom = width / max(page.rect.width, 1)
        page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).save(out)

    def close(self):
        try:
            self._d.close()
        except Exception:
            pass


class AndroidDoc:
    """Lecture PDF sur Android avec le moteur natif PdfRenderer (arm32 et arm64, sans PyMuPDF)."""

    def __init__(self, path):
        from jnius import autoclass
        self._Bitmap = autoclass("android.graphics.Bitmap")
        self._Config = autoclass("android.graphics.Bitmap$Config")
        self._Fmt = autoclass("android.graphics.Bitmap$CompressFormat")
        self._Matrix = autoclass("android.graphics.Matrix")
        self._Canvas = autoclass("android.graphics.Canvas")
        self._FileOut = autoclass("java.io.FileOutputStream")
        file_cls = autoclass("java.io.File")
        pfd_cls = autoclass("android.os.ParcelFileDescriptor")
        renderer_cls = autoclass("android.graphics.pdf.PdfRenderer")
        self._pfd = pfd_cls.open(file_cls(path), pfd_cls.MODE_READ_ONLY)
        try:
            self._r = renderer_cls(self._pfd)
        except Exception as e:
            self._pfd.close()
            if "Password" in str(e) or "Security" in str(e):
                raise PdfLocked()
            raise
        self.page_count = self._r.getPageCount()

    def render_file(self, n, width, out):
        page = self._r.openPage(n)  # une seule page ouverte à la fois
        try:
            w, h = float(page.getWidth()), float(page.getHeight())
            scale = width / max(w, 1.0)
            tw, th = max(int(width), 1), max(int(h * scale), 1)
            bmp = self._Bitmap.createBitmap(tw, th, self._Config.ARGB_8888)
            # fond blanc. NB : eraseColor() existe en version int ET long, pyjnius choisit la mauvaise
            # (JavaException) ; drawRGB(int, int, int) n'a qu'une seule signature.
            canvas = self._Canvas(bmp)
            canvas.drawRGB(255, 255, 255)
            # SANS matrice d'échelle, Android dessine la page à 72 dpi en haut à gauche : le reste reste blanc
            matrix = self._Matrix()
            matrix.setScale(scale, scale)
            page.render(bmp, None, matrix, 1)  # 1 = RENDER_MODE_FOR_DISPLAY
        finally:
            page.close()
        fos = self._FileOut(out)
        try:
            ok = bmp.compress(self._Fmt.PNG, 100, fos)
            fos.flush()
        finally:
            fos.close()
            bmp.recycle()
        if not ok:
            raise RuntimeError("export PNG impossible")

    def close(self):
        for obj in (getattr(self, "_r", None), getattr(self, "_pfd", None)):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass


def pdf_available():
    return platform == "android" or fitz is not None


def open_pdf_doc(path):
    if platform == "android":
        return AndroidDoc(path)
    if fitz is None:
        raise RuntimeError("PyMuPDF manquant : pip install pymupdf")
    return FitzDoc(path)


MAX_PAGE_W = 2000 if platform == "android" else 2600
# dans un .exe PyInstaller, les fichiers sont extraits dans sys._MEIPASS
BASE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
resource_add_path(BASE)
PDF_DIR = os.path.join(BASE, "pdf")

CONTACT_NUMBER = "+227 77811479"
CONTACT_URL = "https://wa.me/22777811479"
PALETTES = ["Brown", "Blue", "Teal", "Green", "Red"]
SKIP_DIRS = {"AppData", "Android", "node_modules", "__pycache__", "venv", "site-packages", "snap", "proc"}

TITLES = {"catalogue": "Catalogue", "favoris": "Favoris", "mes": "Mes PDF"}
EMPTY = {
    "catalogue": "Aucun PDF dans le dossier « pdf ».\nAjoute-en et reviens ici.",
    "favoris": "Aucun favori pour l'instant.\nAppuie sur l'étoile d'un PDF.",
    "mes": "Aucun PDF trouvé sur ce téléphone.",
}

INFO = {
    "politique": (
        "Politique de confidentialité",
        "G-BEPC est une application éducative éditée par G-SOCIETY.\n\n"
        "Données collectées\nAucune. L'application n'a pas de compte, pas de publicité et "
        "n'envoie rien sur Internet.\n\n"
        "Données stockées sur ton téléphone\nTes favoris, la dernière page lue de chaque PDF, "
        "tes alarmes et tes réglages (thème, couleur). Elles restent sur ton appareil et "
        "tu peux les effacer à tout moment dans Paramètres.\n\n"
        "Accès aux fichiers\nLe menu « Mes PDF » demande l'accès au stockage uniquement pour "
        "lire la liste de tes PDF. Tes fichiers ne sont jamais modifiés, copiés ni partagés.\n\n"
        "Contact\nUne question ? Écris à G-SOCIETY au " + CONTACT_NUMBER + ".\n\n"
        "Dernière mise à jour : octobre 2026.",
    ),
    "help": (
        "Aide",
        "Lire un PDF\nAppuie sur un document du catalogue : il s'ouvre directement. "
        "L'application retient la dernière page lue et t'y ramène automatiquement.\n\n"
        "Ajouter des cours\nPose tes fichiers PDF dans le dossier « pdf » de l'application : "
        "ils apparaissent seuls dans le catalogue. La 1re page sert de miniature, le nom du "
        "fichier sert de titre.\n\n"
        "Favoris\nAppuie sur l'étoile d'un document. Retrouve-les dans l'onglet FAVORIS.\n\n"
        "Rechercher\nAppuie sur la loupe et tape une partie du nom (les accents sont ignorés).\n\n"
        "Mes PDF\nScanne ton téléphone pour retrouver les PDF que tu as déjà. "
        "Autorise l'accès aux fichiers quand Android te le demande.\n\n"
        "Alarmes\nProgramme des rappels de révision. Garde l'application ouverte pour qu'ils sonnent.\n\n"
        "Besoin d'aide ?\nContacte G-SOCIETY au " + CONTACT_NUMBER + ".",
    ),
}


# ------------------------------------------------------------------ erreurs en cours d'exécution
class _ErrorHandler(ExceptionHandler):
    """Une erreur dans un bouton ne ferme plus l'app : on la note et on prévient."""
    last = 0.0

    def handle_exception(self, inst):
        text = traceback.format_exc()
        print(text)
        try:
            app = MDApp.get_running_app()
            with open(os.path.join(app.user_data_dir, "crash.txt"), "a", encoding="utf-8") as f:
                f.write(datetime.now().isoformat() + "\n" + text + "\n")
        except Exception:
            pass
        if time.time() - _ErrorHandler.last > 5:
            _ErrorHandler.last = time.time()
            try:
                toast("Erreur : " + str(inst)[:90])
            except Exception:
                pass
        return ExceptionManager.PASS


ExceptionManager.add_handler(_ErrorHandler())


# ------------------------------------------------------------------ utilitaires
def norm(text):
    """minuscules + sans accents, pour une recherche tolérante."""
    text = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def pretty_title(path):
    name = os.path.splitext(os.path.basename(path))[0]
    return " ".join(name.replace("_", " ").replace("-", " ").split()) or name


def list_pdfs(root, max_depth=6):
    """Liste récursive des PDF sous root (dossiers cachés et inutiles ignorés)."""
    found = []
    if not os.path.isdir(root):
        return found
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for cur, dirs, files in os.walk(root):
        depth = cur.count(os.sep) - base_depth
        if depth >= max_depth:
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for f in files:
            if f.lower().endswith(".pdf") and not f.startswith("."):
                found.append(os.path.join(cur, f))
    return found


class Store:
    """Petit stockage JSON (favoris, pages lues, alarmes, réglages)."""

    DEFAULTS = {"favs": [], "last": {}, "dark": True, "palette": "Brown", "alarms": []}

    def __init__(self, path):
        self.path = path
        self.data = json.loads(json.dumps(self.DEFAULTS))
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print("[Store] lecture impossible :", e)

    def save(self, *_):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as e:
            print("[Store] écriture impossible :", e)

    def save_later(self, delay=0.8):
        Clock.unschedule(self.save)
        Clock.schedule_once(self.save, delay)


class ThumbWorker(threading.Thread):
    """Génère les miniatures (page 1) dans un seul thread de fond."""

    def __init__(self, cache_dir):
        super().__init__(daemon=True)
        self.cache_dir = cache_dir
        self.q = queue.Queue()

    def request(self, path, callback):
        self.q.put((path, callback))

    def run(self):
        while True:
            path, callback = self.q.get()
            result = self._make(path)
            Clock.schedule_once(lambda dt, r=result, c=callback: c(r))

    def _make(self, path):
        if not pdf_available():
            return ("", 0)
        doc = None
        try:
            st = os.stat(path)
            key = hashlib.md5(f"{path}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()
            out = os.path.join(self.cache_dir, key + ".png")
            doc = open_pdf_doc(path)
            pages = doc.page_count
            if not os.path.exists(out):
                part = out[:-4] + ".part.png"
                doc.render_file(0, 260, part)
                os.replace(part, out)
            return (out, pages)
        except Exception as e:  # PDF corrompu ou protégé : on n'arrête jamais le worker
            print("[Thumb]", path, e)
            return ("", 0)
        finally:
            if doc is not None:
                doc.close()


# ------------------------------------------------------------------ widgets
class PdfCard(MDCard):
    path = StringProperty()
    title = StringProperty()
    thumb = StringProperty("assets/G.jpg")
    sub = StringProperty("")
    fav = BooleanProperty(False)
    pages = NumericProperty(0)

    def refresh_sub(self):
        app = MDApp.get_running_app()
        last = app.store.data["last"].get(self.path, 0)
        parts = []
        if self.pages:
            parts.append(f"{self.pages} pages")
            if last > 0:
                parts.append(f"reprendre p. {last + 1}")
        self.sub = " · ".join(parts)

    def on_thumb_ready(self, result):
        png, pages = result
        if png:
            self.thumb = png
        if pages:
            self.pages = pages
        self.refresh_sub()

    def toggle_fav(self):
        app = MDApp.get_running_app()
        self.fav = app.toggle_fav(self.path)
        home = app.root.get_screen("home")
        if home.mode == "favoris" and not self.fav:
            home.remove_card(self)


class AlarmRow(MDCard):
    uid = StringProperty("")
    time_text = StringProperty("")
    note = StringProperty("")
    alarm_on = BooleanProperty(True)

    def _find(self):
        app = MDApp.get_running_app()
        for a in app.store.data["alarms"]:
            if a.get("id") == self.uid:
                return a
        return None

    def toggle(self, value):
        a = self._find()
        if a is None or a["on"] == value:
            return
        a["on"] = value
        MDApp.get_running_app().store.save_later()

    def remove(self):
        app = MDApp.get_running_app()
        app.store.data["alarms"] = [a for a in app.store.data["alarms"] if a.get("id") != self.uid]
        app.store.save()
        app.root.get_screen("alarms").refresh()


# ------------------------------------------------------------------ écrans
class IntroScreen(MDScreen):
    def on_enter(self, *args):
        # les ids ne sont pas encore remplis pendant le chargement du KV : on attend une frame
        Clock.schedule_once(self._animate, 0)

    def _animate(self, dt):
        logo = self.ids.get("logo")
        if logo is None:
            return
        logo.opacity = 0
        logo.size = (dp(90), dp(90))
        Animation(opacity=1, size=(dp(140), dp(140)), d=0.7, t="out_back").start(logo)


class Home(MDScreen):
    mode = StringProperty("catalogue")
    section_title = StringProperty("Catalogue")

    def __init__(self, **kwargs):
        self._items = []
        self._queue = []
        self._token = 0
        self.query = ""
        self._started = False
        super().__init__(**kwargs)

    def on_pre_enter(self, *args):
        if not self._started:
            self._started = True
            self.set_mode("catalogue")
        else:
            self.refresh_subs()

    # -- navigation
    def nav(self, target):
        self.ids.nav_drawer.set_state("close")
        if target in TITLES:
            self.set_mode(target)
        else:
            MDApp.get_running_app().goto(target)

    def set_mode(self, mode):
        app = MDApp.get_running_app()
        self.mode = mode
        self.section_title = TITLES[mode]
        self._token += 1  # invalide un scan "Mes PDF" encore en cours
        self.ids.spinner.active = False
        if mode == "catalogue":
            self._items = sorted(list_pdfs(PDF_DIR), key=lambda p: norm(pretty_title(p)))
            self._render()
        elif mode == "favoris":
            self._items = [p for p in app.store.data["favs"] if os.path.isfile(p)]
            self._render()
        else:
            self._items = []
            self._render()
            self.ids.spinner.active = True
            self.ids.empty.text = "Recherche de tes PDF…"
            token = self._token
            app.ask_storage(lambda: threading.Thread(
                target=self._scan, args=(token,), daemon=True).start())

    def _scan(self, token):
        try:
            result = MDApp.get_running_app().scan_device()
        except Exception as e:
            print("[Scan]", e)
            result = []
        Clock.schedule_once(lambda dt: self._scan_done(token, result))

    def _scan_done(self, token, result):
        if token != self._token:
            return
        self.ids.spinner.active = False
        self._items = result
        self._render()

    # -- recherche
    def toggle_search(self):
        ids = self.ids
        if ids.search_box.height < dp(10):
            ids.search_box.disabled = False
            Animation(height=dp(64), opacity=1, d=0.2).start(ids.search_box)
            Clock.schedule_once(lambda dt: setattr(ids.search, "focus", True), 0.25)
        else:
            ids.search.focus = False
            ids.search.text = ""
            ids.search_box.disabled = True
            Animation(height=0, opacity=0, d=0.2).start(ids.search_box)

    def apply_filter(self, text):
        self.query = norm((text or "").strip())
        self._render()

    # -- affichage progressif de la liste
    def _render(self):
        ids = self.ids
        visible = [p for p in self._items if self.query in norm(pretty_title(p))
                   or self.query in norm(os.path.basename(p))]
        ids.list_box.clear_widgets()
        Clock.unschedule(self._feed)
        self._queue = visible[:]
        if not ids.spinner.active:
            if visible:
                ids.empty.text = ""
            else:
                ids.empty.text = "Aucun résultat." if self.query else EMPTY[self.mode]
        if visible:
            Clock.schedule_interval(self._feed, 0)

    def _feed(self, dt):
        app = MDApp.get_running_app()
        box = self.ids.list_box
        for _ in range(5):
            if not self._queue:
                return False
            path = self._queue.pop(0)
            card = PdfCard(path=path, title=pretty_title(path),
                           fav=path in app.store.data["favs"])
            card.refresh_sub()
            card.opacity = 0
            box.add_widget(card)
            Animation(opacity=1, d=0.25).start(card)
            app.thumbs.request(path, card.on_thumb_ready)
        return True

    def remove_card(self, card):
        if card.path in self._items:
            self._items.remove(card.path)
        anim = Animation(opacity=0, height=0, d=0.2)
        anim.bind(on_complete=lambda *a: self._after_remove(card))
        anim.start(card)

    def _after_remove(self, card):
        self.ids.list_box.remove_widget(card)
        if not self.ids.list_box.children and not self._queue:
            self.ids.empty.text = EMPTY[self.mode]

    def refresh_subs(self):
        favs = MDApp.get_running_app().store.data["favs"]
        for c in self.ids.list_box.children:
            if isinstance(c, PdfCard):
                c.fav = c.path in favs
                c.refresh_sub()


class ReaderScreen(MDScreen):
    doc_title = StringProperty("")
    page_label = StringProperty("")
    star_icon = StringProperty("star-outline")
    page_no = NumericProperty(0)
    page_count = NumericProperty(1)

    def __init__(self, **kwargs):
        self.doc = None
        self.path = ""
        self.zoom = 1.0
        super().__init__(**kwargs)

    def open_doc(self, path):
        app = MDApp.get_running_app()
        if not pdf_available():
            toast("PyMuPDF manquant : pip install pymupdf")
            return False
        self.close_doc()
        try:
            doc = open_pdf_doc(path)
            if doc.page_count < 1:
                doc.close()
                toast("PDF vide")
                return False
        except PdfLocked:
            toast("Ce PDF est protégé par un mot de passe")
            return False
        except Exception:
            app.show_error("Impossible d'ouvrir ce PDF", traceback.format_exc())
            return False
        self.doc, self.path, self.zoom = doc, path, 1.0
        self.doc_title = pretty_title(path)
        self.star_icon = "star" if path in app.store.data["favs"] else "star-outline"
        self.page_count = doc.page_count
        self.show_page(app.store.data["last"].get(path, 0))
        return True

    def close_doc(self):
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = None

    def show_page(self, n):
        if self.doc is None:
            return
        app = MDApp.get_running_app()
        n = max(0, min(int(n), self.doc.page_count - 1))
        try:
            target_w = min(Window.width * self.zoom * 1.5, MAX_PAGE_W)
            out = os.path.join(app.cache_dir, "_page.png")
            self.doc.render_file(n, target_w, out)
            tex = CoreImage(out, nocache=True).texture
        except Exception:
            app.show_error("Page illisible", traceback.format_exc())
            return
        img = self.ids.page_img
        img.texture = tex
        img.width = Window.width * self.zoom
        img.height = img.width * tex.height / tex.width
        self.page_no = n
        self.page_label = f"{n + 1} / {self.doc.page_count}"
        self.ids.scroll.scroll_y = 1
        app.store.data["last"][self.path] = n
        app.store.save_later()
        img.opacity = 0
        Animation(opacity=1, d=0.15).start(img)

    def go_to(self, n):
        self.show_page(n)

    def prev(self):
        self.show_page(self.page_no - 1)

    def next(self):
        self.show_page(self.page_no + 1)

    def zoom_in(self):
        self.zoom = min(3.0, self.zoom + 0.5)
        self.show_page(self.page_no)

    def zoom_out(self):
        self.zoom = max(1.0, self.zoom - 0.5)
        self.show_page(self.page_no)

    def toggle_fav(self):
        if self.path:
            fav = MDApp.get_running_app().toggle_fav(self.path)
            self.star_icon = "star" if fav else "star-outline"

    def close(self):
        app = MDApp.get_running_app()
        app.store.save()
        self.close_doc()
        app.goto("home", "right")


class AlarmsScreen(MDScreen):
    def on_pre_enter(self, *args):
        self.refresh()

    def refresh(self):
        app = MDApp.get_running_app()
        box = self.ids.alarm_box
        box.clear_widgets()
        alarms = sorted(app.store.data["alarms"], key=lambda a: a["time"])
        self.ids.alarm_empty.text = "" if alarms else "Aucune alarme.\nAppuie sur + pour en créer une."
        for a in alarms:
            box.add_widget(AlarmRow(uid=a["id"], time_text=a["time"],
                                    note=a.get("note", ""), alarm_on=a["on"]))

    def add(self):
        picker = MDTimePicker()
        picker.bind(on_save=self._saved)
        picker.open()

    def _saved(self, picker, time):
        app = MDApp.get_running_app()
        app.store.data["alarms"].append({
            "id": uuid.uuid4().hex[:8], "time": time.strftime("%H:%M"),
            "note": "Séance de révision", "on": True, "fired": ""})
        app.store.save()
        self.refresh()


class ParamsScreen(MDScreen):
    pass


class InfoScreen(MDScreen):
    kind = StringProperty("")
    title = StringProperty("")
    body = StringProperty("")

    def on_kind(self, *args):
        self.title, self.body = INFO.get(self.kind, ("", ""))


# ------------------------------------------------------------------ application
class MyApp(MDApp):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title = "G-BEPC"
        self.icon = os.path.join(BASE, "assets", "icon.png")
        self.kv_file = os.path.join(BASE, "my.kv")
        self.store = Store(os.path.join(self.user_data_dir, "state.json"))
        self.cache_dir = os.path.join(self.user_data_dir, "thumbs")
        os.makedirs(self.cache_dir, exist_ok=True)
        palette = self.store.data.get("palette", "Brown")
        self.theme_cls.primary_palette = palette if palette in PALETTES else "Brown"
        self.theme_cls.primary_hue = "500"
        self.theme_cls.theme_style = "Dark" if self.store.data.get("dark", True) else "Light"
        self.thumbs = ThumbWorker(self.cache_dir)
        self.thumbs.start()
        self._ring_dialog = None
        self._err_dialog = None
        Window.softinput_mode = "below_target"

    def build(self):
        # my.kv est chargé automatiquement (MyApp -> my.kv) : on ne retourne rien.
        return None

    def on_start(self):
        Clock.schedule_interval(self.check_alarms, 15)
        Window.bind(on_keyboard=self.on_key)

    def on_pause(self):
        self.store.save()
        return True

    def on_stop(self):
        self.store.save()

    # -- navigation
    def goto(self, name, direction="left"):
        sm = self.root
        if direction == "fade":
            sm.transition = FadeTransition(duration=0.3)
        else:
            sm.transition = SlideTransition(direction=direction, duration=0.25)
        sm.current = name

    def start(self):
        self.goto("home", "fade")

    def open_pdf(self, path):
        reader = self.root.get_screen("reader")
        if reader.open_doc(path):
            self.goto("reader", "left")

    def on_key(self, window, key, *args):
        if key != 27:  # Échap / retour Android
            return False
        sm = self.root
        cur = sm.current
        if cur == "reader":
            sm.get_screen("reader").close()
            return True
        if cur in ("alarms", "params", "politique", "help"):
            self.goto("home", "right")
            return True
        if cur == "home":
            home = sm.get_screen("home")
            if home.ids.nav_drawer.state == "open":
                home.ids.nav_drawer.set_state("close")
                return True
            if home.mode != "catalogue":
                home.set_mode("catalogue")
                return True
        return False

    # -- réglages
    def swicht(self, obj=None):
        self.set_dark(self.theme_cls.theme_style == "Light")

    def set_dark(self, dark):
        self.theme_cls.theme_style = "Dark" if dark else "Light"
        self.store.data["dark"] = bool(dark)
        self.store.save_later()

    def set_palette(self, name):
        if name in PALETTES:
            self.theme_cls.primary_palette = name
            self.store.data["palette"] = name
            self.store.save_later()

    def clear_history(self):
        self.store.data["last"] = {}
        self.store.save()
        self.root.get_screen("home").refresh_subs()
        toast("Historique de lecture effacé")

    def clear_cache(self):
        try:
            shutil.rmtree(self.cache_dir, ignore_errors=True)
            os.makedirs(self.cache_dir, exist_ok=True)
            toast("Miniatures supprimées (recréées à la prochaine ouverture)")
        except OSError as e:
            print("[Cache]", e)
            toast("Impossible de vider le cache")

    def contact(self):
        try:
            webbrowser.open(CONTACT_URL)
        except Exception:
            toast("Contact G-SOCIETY : " + CONTACT_NUMBER)

    # -- favoris
    def toggle_fav(self, path):
        favs = self.store.data["favs"]
        if path in favs:
            favs.remove(path)
            state = False
        else:
            favs.append(path)
            state = True
        self.store.save_later()
        return state

    # -- stockage / scan du téléphone
    def ask_storage(self, then):
        """Demande les permissions Android puis appelle then() (toujours appelé)."""
        if platform != "android":
            then()
            return
        try:
            from android.permissions import Permission, request_permissions

            def done(perms, grants):
                Clock.schedule_once(lambda dt: self._all_files_access(then))

            request_permissions([Permission.READ_EXTERNAL_STORAGE,
                                 Permission.WRITE_EXTERNAL_STORAGE], done)
        except Exception as e:
            print("[Perm]", e)
            then()

    def _all_files_access(self, then):
        try:
            from jnius import autoclass
            version = autoclass("android.os.Build$VERSION")
            if version.SDK_INT >= 30:
                env = autoclass("android.os.Environment")
                if not env.isExternalStorageManager():
                    intent_cls = autoclass("android.content.Intent")
                    settings = autoclass("android.provider.Settings")
                    activity = autoclass("org.kivy.android.PythonActivity").mActivity
                    activity.startActivity(
                        intent_cls(settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
                    toast("Autorise « Accès à tous les fichiers », puis rouvre Mes PDF")
        except Exception as e:
            print("[AllFiles]", e)
        then()

    def scan_device(self):
        if platform == "android":
            roots = ["/storage/emulated/0"]
        else:
            home = os.path.expanduser("~")
            roots = [home]
            shared = os.path.join(home, "storage", "shared")
            if os.path.isdir(shared):
                roots.append(shared)
        catalogue = os.path.realpath(PDF_DIR)
        seen, out = set(), []
        for root in roots:
            for p in list_pdfs(root, max_depth=6):
                real = os.path.realpath(p)
                if real in seen or real.startswith(catalogue + os.sep):
                    continue
                seen.add(real)
                out.append(p)
        out.sort(key=lambda p: norm(pretty_title(p)))
        return out

    # -- erreurs et diagnostic (utile sans ordinateur)
    def show_error(self, title, text):
        print(text)
        short = text.strip()[-700:].replace("[", "(").replace("]", ")")

        def copy(*_):
            try:
                Clipboard.copy(text)
                toast("Texte copié")
            except Exception:
                pass

        if self._err_dialog is not None:
            self._err_dialog.dismiss()
        self._err_dialog = MDDialog(
            title=title, text=short,
            buttons=[MDFlatButton(text="COPIER", on_release=copy),
                     MDFlatButton(text="FERMER", on_release=lambda *_: self._err_dialog.dismiss())])
        self._err_dialog.open()

    def diagnostic(self):
        engine = "PdfRenderer (Android)" if platform == "android" else ("PyMuPDF" if fitz else "aucun")
        lines = [f"plateforme : {platform}", f"moteur PDF : {engine}",
                 f"dossier pdf existe : {os.path.isdir(PDF_DIR)}"]
        try:
            pdfs = list_pdfs(PDF_DIR)
            lines.append(f"PDF du catalogue : {len(pdfs)}")
            if pdfs:
                p = pdfs[0]
                lines.append("test sur : " + os.path.basename(p)[:40])
                doc = open_pdf_doc(p)
                try:
                    lines.append(f"pages : {doc.page_count}")
                    out = os.path.join(self.cache_dir, "_diag.png")
                    doc.render_file(0, 600, out)
                finally:
                    doc.close()
                lines.append(f"fichier png : {os.path.getsize(out)} octets")
                tex = CoreImage(out, nocache=True).texture
                lines.append(f"texture : {tex.width}x{tex.height}")
                try:
                    from PIL import Image as PILImage
                    hist = PILImage.open(out).convert("L").histogram()
                    lines.append(f"pixels non blancs : {100 * sum(hist[:245]) / max(sum(hist), 1):.1f} %")
                except Exception as e:
                    lines.append(f"analyse image impossible : {e}")
        except Exception:
            lines.append(traceback.format_exc())
        self.show_error("Diagnostic", "\n".join(lines))

    # -- alarmes
    def check_alarms(self, dt):
        now = datetime.now()
        hm, stamp = now.strftime("%H:%M"), now.strftime("%Y%m%d%H%M")
        for a in self.store.data["alarms"]:
            if a.get("on") and a.get("time") == hm and a.get("fired") != stamp:
                a["fired"] = stamp
                self.store.save_later()
                self.ring(a)

    def ring(self, alarm):
        try:
            from plyer import vibrator
            vibrator.vibrate(1.5)
        except Exception:
            pass
        if self._ring_dialog is not None:
            self._ring_dialog.dismiss()
        self._ring_dialog = MDDialog(
            title="Alarme " + alarm.get("time", ""),
            text=alarm.get("note") or "C'est l'heure de réviser !",
            buttons=[MDFlatButton(text="OK", on_release=lambda *_: self._ring_dialog.dismiss())],
        )
        self._ring_dialog.open()


if __name__ == "__main__":
    MyApp().run()
