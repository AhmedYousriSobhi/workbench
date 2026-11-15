"""The README's animated tours (#799): a pointer that moves, click ripples and slide transitions over the real page,
driven by scripts/doc_shots.py with sample data. Nothing here is shipped: the pointer, the ripple and the caption are
added to the page only while it is captured, and their colours are the page's own tokens (palette.yaml, ADR 0069).

Each frame is one screenshot. The pointer moves the real mouse too, so hover styles show as a visitor would see them.
A smooth scroll is drawn frame by frame; a slide (the page pushed sideways, as a swipe would) is composed from two
screenshots. All frames share one palette, so a GIF frame stores only the pixels that changed."""
import io
import math

MS = 70  # one frame: about 14 frames a second

ZOOM_STEPS = ("Zoom out: the Fleet, one card per cluster",  # the zoom tour
              "Click the cluster: every part and wire",
              "Layers: pick one, only its wires stay lit",
              "Open a pool: one card per node",
              "Zoom back out to the Cluster")

STEPS = ("Live: wires pulse with requests, red dashes are errors",  # the main tour, one caption per step (tests/test_docs.py)
         "Lenses and layouts: one plane at a time, or the 2.5D view",
         "Click a node: how to reach it, ready to copy",
         "Break it: power it off and see the blast radius",
         "Zoom out to the Fleet, click a cluster to dive back in",
         "Swipe to Traffic & Communication: who called what")

OVERLAY = """([pos, mid]) => {
  for (const id of ['demo-cursor', 'demo-ripple', 'demo-cap']) document.getElementById(id)?.remove();
  const top = 'position:fixed;pointer-events:none;z-index:2147483647;';
  const c = document.createElement('div'); c.id = 'demo-cursor'; c.setAttribute('aria-hidden', 'true');
  c.style.cssText = top + 'left:0;top:0;width:36px;height:36px;transform:translate(-80px,-80px);filter:drop-shadow(0 2px 3px var(--shade-35))';
  c.innerHTML = '<svg viewBox="0 0 30 30" width="36" height="36"><path d="M4 2 L4 24 L9.6 18.8 L13.6 27.4 L17.6 25.6 L13.7 17.2 L21 17.2 Z"/></svg>';
  c.querySelector('path').style.cssText = 'fill:var(--fg);stroke:var(--panel);stroke-width:1.8;stroke-linejoin:round';  // CSSOM: the page's CSP drops inline style attributes
  const r = document.createElement('div'); r.id = 'demo-ripple'; r.setAttribute('aria-hidden', 'true');
  r.style.cssText = top + 'left:0;top:0;width:60px;height:60px;margin:-30px 0 0 -30px;border-radius:50%;border:3px solid var(--accent);background:var(--sel);opacity:0';
  const k = document.createElement('div'); k.id = 'demo-cap'; k.setAttribute('aria-hidden', 'true');
  k.style.cssText = top + `left:${mid}px;${pos}:22px;transform:translateX(-50%);display:flex;align-items:center;gap:12px;padding:9px 18px 9px 9px;border-radius:999px;` +
    'background:var(--panel);color:var(--fg);border:2px solid var(--accent);box-shadow:0 6px 18px var(--shade-25);font:600 21px system-ui,sans-serif;white-space:nowrap';
  k.innerHTML = '<b></b><span></span>';
  k.firstChild.style.cssText = 'background:var(--accent-fill);color:var(--on-accent);border-radius:999px;padding:3px 12px;font-size:18px';
  document.body.append(r, c, k);
}"""


def ease(t):
    """Ease in and out (cubic): slow start, slow stop, as a hand moves."""
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def glide(t):
    """Ease in and out (sine), gentler than ease(): a move drawn in four or five frames still reads as one motion."""
    return (1 - math.cos(math.pi * t)) / 2


class Tour:
    """Records frames from a Playwright page. crop is (left, top, right, bottom) in CSS pixels; width the GIF width."""

    def __init__(self, page, width, crop=None, caption="bottom"):
        self.pg, self.width, self.crop, self.frames, self.at = page, width, crop, [], (-80.0, -80.0)
        page.evaluate(OVERLAY, [caption, (crop[0] + crop[2]) / 2 if crop else page.viewport_size["width"] / 2])

    def shot(self):
        from PIL import Image
        im = Image.open(io.BytesIO(self.pg.screenshot())).convert("RGB")
        if self.crop:
            im = im.crop(self.crop)
        return im.resize((self.width, round(im.height * self.width / im.width)), Image.LANCZOS)

    def snap(self, n=1, each=None):
        """n frames of whatever the page does now; each(i) runs before frame i (a traffic dot, say)."""
        for i in range(n):
            if each:
                each(i)
            self.frames.append(self.shot())

    def caption(self, text, step=None):
        self.pg.evaluate("([s, t]) => { const k = document.getElementById('demo-cap'); k.firstChild.textContent = s; k.firstChild.hidden = !s; k.lastChild.textContent = t; }", [step or "", text])

    def _cursor(self, x, y, press=False):
        self.at = (x, y)
        self.pg.evaluate("([x, y, s]) => { document.getElementById('demo-cursor').style.transform = `translate(${x - 4}px, ${y - 2}px) scale(${s})`; }", [x, y, 0.86 if press else 1])
        if x >= 0 and y >= 0:
            self.pg.mouse.move(x, y)

    def centre(self, sel):
        b = self.pg.locator(sel + " >> visible=true").first.bounding_box()
        return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    def move(self, to, n=10):
        """Glide the pointer to a point or a selector's centre along a gentle curve."""
        x1, y1 = self.centre(to) if isinstance(to, str) else to
        x0, y0 = self.at if self.at[0] >= 0 else (x1 + 260, y1 + 200)
        bend = 0.18 * math.hypot(x1 - x0, y1 - y0)
        for i in range(1, n + 1):
            t = ease(i / n)
            off = bend * 4 * t * (1 - t)  # the curve's sideways lift, largest half way
            self._cursor(x0 + (x1 - x0) * t - off * 0.35, y0 + (y1 - y0) * t - off)
            self.frames.append(self.shot())

    def click(self, to, n=10, after=5, dbl=False, act=None):
        """Move there, press (the pointer dips), click, and let a ripple spread while the page answers."""
        self.move(to, n)
        x, y = self.at
        self._cursor(x, y, press=True)
        self.pg.evaluate("([x, y]) => { const r = document.getElementById('demo-ripple'); r.style.left = x + 'px'; r.style.top = y + 'px'; r.style.transform = 'scale(0.3)'; r.style.opacity = 0.95; }", [x, y])
        self.frames.append(self.shot())
        (act or (lambda: self.pg.mouse.dblclick(x, y) if dbl else self.pg.mouse.click(x, y)))()
        self._cursor(x, y)
        for i in range(1, after + 1):
            t = i / after
            self.pg.evaluate("([s, o]) => { const r = document.getElementById('demo-ripple'); r.style.transform = `scale(${s})`; r.style.opacity = o; }", [0.3 + 1.1 * t, round(0.95 * (1 - t), 3)])
            self.frames.append(self.shot())

    def scroll(self, y, n=4):
        y0 = self.pg.evaluate("scrollY")
        for i in range(1, n + 1):
            self.pg.evaluate("y => scrollTo({top: y, behavior: 'instant'})", y0 + (y - y0) * glide(i / n))
            self.frames.append(self.shot())

    def reveal(self, sel, at=0.4, n=4):
        """Scroll smoothly until sel's top sits at that fraction of the window, clear of the caption."""
        self.scroll(self.pg.evaluate("([s, at]) => scrollY + document.querySelector(s).getBoundingClientRect().top - innerHeight * at", [sel, at]), n)

    def slide(self, change, n=5, back=False, region="#main"):
        """A swipe: change() switches the view and the new content pushes the old one out sideways. The header, the
        side bar and the caption stay put (they come from the new view), as they would on the page."""
        cap_on = "v => { document.getElementById('demo-cap').style.visibility = v ? '' : 'hidden'; }"
        self.pg.evaluate(cap_on, False)  # the content slides without the caption, which is pasted on top, still
        old = self.shot()
        change()
        new = self.shot()
        self.pg.evaluate(cap_on, True)
        held = self.shot()
        k = self.width / ((self.crop[2] - self.crop[0]) if self.crop else self.pg.viewport_size["width"])
        r = self.pg.locator(region).bounding_box()
        left, top = max(0, round(r["x"] * k) - 8), max(0, round(self.pg.locator("header.top").bounding_box()["height"] * k))
        c = self.pg.locator("#demo-cap").bounding_box()
        cap = tuple(round(v * k) for v in (c["x"] - 3, c["y"] - 3, c["x"] + c["width"] + 3, c["y"] + c["height"] + 3))
        w = old.width - left
        a, b = old.crop((left, top, old.width, old.height)), new.crop((left, top, new.width, new.height))
        for i in range(1, n + 1):
            dx = round(w * glide(i / n))
            f = new.copy()
            f.paste(a, (left + (dx if back else -dx), top))
            f.paste(b, (left + ((dx - w) if back else (w - dx)), top))
            f.paste(old.crop((0, 0, left, old.height)) if i < n else new.crop((0, 0, left, new.height)), (0, 0))
            f.paste(new.crop((0, 0, new.width, top)), (0, 0))
            f.paste(held.crop(cap), cap[:2])
            self.frames.append(f)

    def save(self, path, colours=56):
        """One shared palette (no dithering), so a frame keeps only the pixels that changed; loops for ever."""
        from PIL import Image
        sample = self.frames[:: max(1, len(self.frames) // 24)]
        sheet = Image.new("RGB", (self.width, sum(f.height for f in sample)))
        y = 0
        for f in sample:
            sheet.paste(f, (0, y)); y += f.height
        pal = sheet.quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        frames = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in self.frames]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=MS, loop=0, optimize=True, disposal=1)


GOV = "closeGov(); render({ok: true, level: 0, mem_available: 9.4 * 2 ** 30, psi_some: 0.3, violations: [], audit: [], lab: null, dry_run: false})"  # a calm governor, whatever the host runs


def dots(pg, to, n=2, err=None, cursor=[400]):  # noqa: B006  a burst of requests (err: error answers) as the flow feed sends them
    cursor[0] += 1
    e = f", err: {err}" if err is not None else ""
    pg.evaluate(f"onFlow({{level: 0, cursor: {cursor[0]}, events: [{{from: 'clients', to: '{to}', n: {n}, label: 'requests', nodes: []{e}}}], totals: flowSnap.totals, errors: flowSnap.errors, reachable: flowSnap.reachable}})")


LIVE = 170  # the main tour's resting scroll: the whole diagram, from the lenses down to the services the dots travel to


def readme_tour(pg, path, healthy, off):
    """The main tour. pg shows the circuit at 1280x800 with sample data; healthy and off switch what /api/cluster answers."""
    pg.evaluate(GOV); pg.evaluate(f"scrollTo(0, {LIVE})"); pg.wait_for_timeout(300)
    t = Tour(pg, 820)
    n = len(STEPS)

    def step(i):
        t.caption(STEPS[i], f"{i + 1}/{n}")

    def traffic(i):
        if i % 4 == 0:
            dots(pg, ("gpu-exporter", "teleport", "bmc-farm", "jira-mock", "bcm-mock")[(i // 4) % 5], 3, 1 if i % 12 == 8 else None)

    def scrolled(sel):  # the page jumps to show what a click opened; draw that jump as a smooth scroll
        def act():
            y0 = pg.evaluate("scrollY"); pg.click(sel); pg.wait_for_timeout(350)
            act.y = pg.evaluate("scrollY"); pg.evaluate("y => scrollTo({top: y, behavior: 'instant'})", y0)
        return act

    pg.evaluate("buildFlow()"); pg.wait_for_timeout(1200)  # drawn again with the sample feed's answers: the Teleport proxy is up
    step(0); t.snap(6, traffic); t.move("#flow [data-part=teleport]", 12); t.snap(4, lambda i: traffic(i + 3))
    step(1); t.click("#lens [data-lens=client]", 9); t.snap(7)
    t.click("#vw-iso", 8, after=4); t.snap(9); t.click("#vw-cv", 7, after=3); t.click("#lens [data-lens=all]", 6, after=3)
    step(2); a = scrolled("#flow [data-ent=cn003]"); t.click("#flow [data-ent=cn003]", 9, act=a); t.scroll(a.y)
    t.move("#pop-copy", 8); t.snap(9)
    step(3); t.click("text=Actions and details", 8, after=4); t.reveal("#nodeacts-fault", 0.45)
    off(); t.click("#nodeacts-fault button[data-action=power-off]", 8)
    pg.evaluate("onFlow({level: 0, cursor: 900, events: [{from: 'injector', to: 'bmc-farm', n: 1, label: 'power-off on cn003', nodes: ['cn003']}], totals: flowSnap.totals, errors: flowSnap.errors, reachable: flowSnap.reachable})")
    t.snap(6); t.scroll(LIVE); t.move("#flow [data-ent=cn003]", 8); t.snap(4); t.move("#flow [data-part=bmc-farm]", 8); t.snap(8); t.scroll(0)

    def calm():  # clear the fault as the page zooms away, so the Fleet card shows a healthy cluster
        healthy(); pg.evaluate("setBlast(null); leaveNode(); hidePop(false); flowSnap.faults = 0; lastCluster = ''; loadCluster()"); pg.click("#zr-out")
    step(4); t.click("#zr-out", 10, act=calm); t.snap(9); t.click("#fleetview .fl-head", 10); t.snap(9)
    step(5); t.click("#tb-traffic", 11, after=3, act=lambda: None)
    t.slide(lambda: (pg.click("#tb-traffic"), pg.wait_for_timeout(2500)))
    t.move((900, 560), 12); t.snap(10)

    def home():  # back to where the loop starts
        pg.click("#tb-nodes"); pg.wait_for_selector("#flow[data-cv] .cv-box"); pg.wait_for_timeout(600); pg.evaluate(f"scrollTo(0, {LIVE})")
        t._cursor(-80, -80); pg.mouse.move(1, 1); step(0)
    t.slide(home, back=True)
    t.save(path)


def zoom_tour(pg, path):
    """The zoom tour, cropped to the diagram. pg shows the circuit at 1280x980 with sample data (room for the caption
    below the layer buttons)."""
    pg.evaluate(GOV); pg.evaluate("buildFlow()"); pg.wait_for_timeout(900)
    pg.evaluate("y => scrollTo({top: y, behavior: 'instant'})", pg.evaluate("scrollY + document.getElementById('zr-out').getBoundingClientRect().top - 74"))
    m = pg.locator("#trafficpanel").bounding_box()
    t = Tour(pg, 820, crop=(round(m["x"]) - 12, 62, round(m["x"] + m["width"]) + 12, pg.viewport_size["height"]))
    n = len(ZOOM_STEPS)

    def step(i):
        t.caption(ZOOM_STEPS[i], f"{i + 1}/{n}")
    step(0); t.snap(3); t.click("#zr-out", 12); t.snap(12)
    step(1); t.click("#fleetview .fl-head", 12); t.snap(10)
    step(2)
    for layer in ("access", "management", "monitoring"):
        t.click(f"#cvstack button[data-layer={layer}]", 8, after=4); t.snap(5)
    t.click("#cvstack button[data-layer=all]", 7, after=3)
    step(3); t.click("#flow [data-part=rack-sc] .cv-open", 12); t.snap(12)
    step(4); t.click("#zr-out", 12); t.snap(10)
    t.save(path)
