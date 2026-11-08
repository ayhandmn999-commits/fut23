/* FutbolClip AI — arayüz (bağımlılık yok). Tüm metinler textContent ile eklenir (XSS güvenli). */
"use strict";

const S = { user: null, csrf: document.querySelector('meta[name="csrf"]').content, status: null, regOpen: true,
  timers: new Set(), mode: "search", prefs: loadPrefs(), dockItems: new Map() };

function loadPrefs() {
  try { return Object.assign({ count: 8, platform: "tiktok", layout: "blur", captions: true, max_videos: 3 },
    JSON.parse(localStorage.getItem("fc_prefs") || "{}")); } catch { return { count: 8, platform: "tiktok", layout: "blur", captions: true, max_videos: 3 }; }
}
function savePrefs() { try { localStorage.setItem("fc_prefs", JSON.stringify(S.prefs)); } catch {} }

/* ---------- yardımcılar ---------- */
const SVG = {
  play: '<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22"><path d="M8 5v14l11-7z"/></svg>',
  dl: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 21h16"/></svg>',
  edit: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>',
  search: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
  logo: '<svg viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="#131720"/><rect x="19" y="8" width="26" height="48" rx="7" fill="none" stroke="#22e57a" stroke-width="4"/><path d="M28 24l12 8-12 8z" fill="#22e57a"/></svg>',
  x: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>',
};

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;            // yalnızca sabit SVG için
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected" || k === "open") el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const svgEl = (s) => { const t = document.createElement("span"); t.innerHTML = s; return t.firstElementChild; };
const fmtT = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const fmtSize = (b) => !b ? "—" : b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : (b / 1e6).toFixed(1) + " MB";
const fmtDate = (t) => new Date(t * 1000).toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

async function api(path, { method = "GET", body, form, xhr } = {}) {
  const opt = { method, headers: { "X-CSRF-Token": S.csrf }, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch {}
  if (r.status === 401 && S.user) { S.user = null; go("#/giris"); }
  if (!r.ok) { const e = new Error((data && data.error) || `Sunucu hatası (${r.status})`); e.status = r.status; throw e; }
  return data;
}

function toast(msg, kind = "") {
  const t = h("div", { class: "toast " + kind }, msg);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 8000 : 4500);
}
function every(fn, ms) { const id = setInterval(fn, ms); S.timers.add(id); return id; }
function clearTimers() { S.timers.forEach(clearInterval); S.timers.clear(); }
function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

/* ---------- kabuk ---------- */
const app = document.getElementById("app");
function shell(active, ...content) {
  const link = (href, label, key) => h("a", { href, class: active === key ? "on" : "" }, label);
  return h("div", null,
    h("header", { class: "topbar" }, h("div", { class: "topbar-in" },
      h("a", { class: "brand", href: "#/" }, svgEl(SVG.logo), h("span", null, "FutbolClip ", h("b", null, "AI"))),
      h("nav", { class: "nav" },
        link("#/", "Panel", "panel"), link("#/projeler", "Projelerim", "projeler"),
        link("#/disa-aktarimlar", "Dışa Aktarımlar", "exports"), link("#/servisler", "Servis Durumu", "servisler")),
      h("div", { class: "userbox" }, h("span", { class: "nm" }, S.user.name || S.user.email),
        h("button", { class: "btn sm ghost", onclick: logout }, "Çıkış")))),
    h("main", { class: "main" }, ...content));
}
async function logout() {
  try { const r = await api("/api/auth/logout", { method: "POST" }); S.csrf = r.csrf; } catch {}
  S.user = null; go("#/giris");
}
function mount(node) { app.replaceChildren(node); window.scrollTo(0, 0); }
const fill = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(k => k !== false && k !== null && k !== undefined));

/* ---------- giriş / kayıt ---------- */
function viewAuth(mode = "login") {
  const isReg = mode === "register";
  const err = h("div", { class: "err", hidden: true });
  const email = h("input", { class: "input", type: "email", required: true, autocomplete: "email", placeholder: "ornek@eposta.com" });
  const pw = h("input", { class: "input", type: "password", required: true, minlength: 8, autocomplete: isReg ? "new-password" : "current-password", placeholder: "En az 8 karakter" });
  const name = h("input", { class: "input", type: "text", autocomplete: "name", placeholder: "Adınız" });
  const btn = h("button", { class: "btn primary lg", type: "submit" }, isReg ? "Hesap oluştur" : "Giriş yap");
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.hidden = true; btn.disabled = true;
    try {
      const r = await api(isReg ? "/api/auth/register" : "/api/auth/login", { method: "POST",
        body: { email: email.value, password: pw.value, name: name.value } });
      S.csrf = r.csrf; await boot(); go("#/");
    } catch (ex) { err.textContent = ex.message; err.hidden = false; } finally { btn.disabled = false; }
  } },
    isReg && h("div", null, h("label", { class: "fl" }, "Ad"), name),
    h("div", null, h("label", { class: "fl" }, "E-posta"), email),
    h("div", null, h("label", { class: "fl" }, "Parola"), pw), err, btn);
  mount(h("div", { class: "auth" },
    h("section", { class: "auth-hero" },
      h("div", null, h("div", { class: "brand" }, svgEl(SVG.logo), h("span", null, "FutbolClip ", h("b", null, "AI"))),
        h("h1", null, "Futbol yorumlarından ", h("em", null, "viral"), " kısa videolar."),
        h("p", null, "Konu yaz; yapay zekâ lisanslı videoları bulsun, en heyecanlı anları seçsin, Türkçe altyazılı 9:16 klipler hazırlasın."),
        h("div", { class: "feat" },
          ...[["🔎", "Video Araştırmacısı: konu yaz, videoyu sen bulma"], ["💬", "Otomatik Türkçe altyazı ve konuşmacı algılama"],
            ["📱", "TikTok, Reels ve Shorts için 1080×1920 MP4"], ["✂️", "Her klibi kırp, altyazıyı düzenle"]].map(([i, t]) =>
            h("div", null, h("i", null, i), t)))),
      h("small", { class: "faint" }, "Yalnızca lisanslı veya işleme izniniz olan içeriklerle çalışır.")),
    h("section", { class: "auth-form" }, h("div", { class: "auth-card" },
      h("h2", null, isReg ? "Hesap oluştur" : "Tekrar hoş geldiniz"),
      h("p", { class: "muted", style: "margin:0" }, isReg ? "Dakikalar içinde ilk kliplerinizi üretin." : "Hesabınıza giriş yapın."),
      form,
      h("p", { class: "muted", style: "margin-top:18px;font-size:14px" },
        isReg ? "Zaten hesabınız var mı? " : "Hesabınız yok mu? ",
        (isReg || S.regOpen) ? h("a", { href: isReg ? "#/giris" : "#/kayit" }, isReg ? "Giriş yapın" : "Kayıt olun") : "Kayıtlar kapalı.")))));
}

/* ---------- panel ---------- */
const EXAMPLES = ["Galatasaray Fenerbahçe maçı", "Ronaldo açıklamaları", "Arda Güler performansı", "Futbol yorumcuları maç sonu değerlendirmesi"];

async function viewPanel() {
  mount(shell("panel", h("div", { class: "skel", style: "height:420px" })));
  const [jobs, st] = await Promise.all([api("/api/jobs"), api("/api/status")]);
  S.status = st;
  const ozet = st.ozet;
  const P = S.prefs;

  const common = () => {
    const platBtns = Object.entries(st.platforms).map(([k, v]) =>
      h("button", { type: "button", class: P.platform === k ? "on" : "", onclick: (e) => { P.platform = k; savePrefs(); [...e.target.parentNode.children].forEach(b => b.classList.toggle("on", b === e.target)); } }, v.name));
    const lay = h("select", { class: "input", onchange: (e) => { P.layout = e.target.value; savePrefs(); } },
      Object.entries(st.layouts).map(([k, v]) => h("option", { value: k, selected: P.layout === k }, v)));
    const cnt = h("input", { class: "range", type: "range", min: 5, max: 10, value: P.count, oninput: (e) => { P.count = +e.target.value; cv.textContent = e.target.value; savePrefs(); } });
    const cv = h("b", null, String(P.count));
    const cap = h("input", { type: "checkbox", checked: P.captions, onchange: (e) => { P.captions = e.target.checked; savePrefs(); } });
    return h("div", { class: "opts" },
      h("div", null, h("label", { class: "fl" }, "Klip sayısı: ", cv), cnt),
      h("div", null, h("label", { class: "fl" }, "Platform"), h("div", { class: "seg" }, platBtns)),
      h("div", null, h("label", { class: "fl" }, "Dikey format"), lay),
      h("div", null, h("label", { class: "fl" }, "Altyazı"), h("label", { class: "check" }, cap, "Türkçe altyazıları videoya göm")));
  };
  const payloadBase = () => ({ count: P.count, platform: P.platform, layout: P.layout, captions: P.captions });
  const submitErr = h("div", { class: "err", hidden: true, style: "margin-top:14px" });
  const fail = (e) => { submitErr.textContent = e.message; submitErr.hidden = false; };

  /* --- araştırma modu --- */
  const searchPane = () => {
    const q = h("input", { class: "input", type: "search", placeholder: "Örn. Galatasaray Fenerbahçe maçı", maxlength: 160, autofocus: true });
    const srcs = [["youtube", "YouTube (CC lisanslı)", st.sources.youtube, "YOUTUBE_API_KEY gerekli"],
      ["commons", "Wikimedia Commons", true], ["archive", "Internet Archive", true]];
    const chosen = new Set(srcs.filter(s => s[2]).map(s => s[0]));
    const mv = h("select", { class: "input", style: "width:auto;min-width:90px", onchange: (e) => { P.max_videos = +e.target.value; savePrefs(); } },
      [1, 2, 3, 4, 5].map(n => h("option", { value: n, selected: P.max_videos === n }, n + " video")));
    const ch = h("textarea", { class: "input", placeholder: "Kanal kimlikleri (UC…) — virgül veya boşlukla ayırın", rows: 2 });
    const att = h("input", { type: "checkbox" });
    const btn = h("button", { class: "btn primary lg", type: "submit" }, svgEl(SVG.search), "Araştır ve klipleri üret");
    return h("form", { onsubmit: async (e) => {
      e.preventDefault(); submitErr.hidden = true;
      if (!q.value.trim()) return fail(new Error("Lütfen bir konu yazın."));
      btn.disabled = true;
      try {
        const r = await api("/api/jobs/search", { method: "POST", body: { ...payloadBase(), query: q.value.trim(), sources: [...chosen],
          max_videos: +mv.value, allowed_channels: ch.value.trim(), attested: att.checked } });
        go("#/proje/" + r.id);
      } catch (ex) { fail(ex); btn.disabled = false; }
    } },
      h("div", { class: "searchbar" }, h("div", { class: "grow" }, q), btn),
      h("div", { class: "chips" }, EXAMPLES.map(t => h("button", { type: "button", class: "chip", onclick: () => { q.value = t; q.focus(); } }, t))),
      h("div", { class: "src-list" },
        srcs.map(([k, label, on, why]) => h("label", { class: "src" + (on ? "" : " off"), title: on ? "" : why },
          h("input", { type: "checkbox", disabled: !on, checked: on, onchange: (e) => e.target.checked ? chosen.add(k) : chosen.delete(k) }),
          label, !on && h("span", { class: "pill warn" }, "anahtar yok"))),
        h("label", { class: "src", style: "cursor:default" }, "Analiz edilecek", mv)),
      !st.sources.youtube && h("div", { class: "notice warn" }, h("span", null, "⚠"),
        h("span", null, h("b", null, "YouTube araması kapalı: "), "YOUTUBE_API_KEY tanımlı değil. Şimdilik Wikimedia Commons ve Internet Archive taranır. ",
          h("a", { href: "#/servisler" }, "Servis durumunu gör"))),
      h("details", { class: "adv" }, h("summary", null, "Gelişmiş: standart lisanslı ama izinli kanallar"),
        h("p", { class: "muted", style: "margin:0 0 10px;font-size:13px" }, "Kendi kanalınız ya da yazılı izin aldığınız kanalların videoları, CC lisansı olmasa da işlenebilir."),
        ch, h("label", { class: "check", style: "margin-top:10px" }, att, "Bu kanalların içerik sahibi olduğumu veya işleme izni aldığımı beyan ederim.")),
      common(), submitErr);
  };

  /* --- bağlantı modu --- */
  const urlPane = () => {
    const u = h("input", { class: "input", type: "url", placeholder: "https://www.youtube.com/watch?v=…", required: true });
    const q = h("input", { class: "input", placeholder: "İsteğe bağlı: ne tür anlar aransın? (ör. gol, penaltı, tartışma)" });
    const att = h("input", { type: "checkbox" });
    const btn = h("button", { class: "btn primary lg", type: "submit" }, "Analiz et ve klipleri üret");
    return h("form", { onsubmit: async (e) => {
      e.preventDefault(); submitErr.hidden = true; btn.disabled = true;
      try { const r = await api("/api/jobs/url", { method: "POST", body: { ...payloadBase(), url: u.value.trim(), query: q.value.trim(), attested: att.checked } }); go("#/proje/" + r.id); }
      catch (ex) { fail(ex); btn.disabled = false; }
    } },
      h("div", { class: "searchbar" }, h("div", { class: "grow" }, u), btn), h("div", { style: "margin-top:12px" }, q),
      h("label", { class: "check", style: "margin-top:14px" }, att, "Bu video CC lisanslı değilse: içerik sahibi olduğumu veya işleme izni aldığımı beyan ederim."),
      !ozet.youtube_indirme && h("div", { class: "notice warn" }, h("span", null, "⚠"), h("span", null, h("b", null, "yt-dlp kurulu değil: "), "YouTube videosu indirilemez. ", h("a", { href: "#/servisler" }, "Ayrıntı"))),
      common(), submitErr);
  };

  /* --- yükleme modu --- */
  const uploadPane = () => {
    let file = null, subs = null;
    const fi = h("input", { type: "file", accept: "video/*,.mkv,.ts", hidden: true, onchange: (e) => setFile(e.target.files[0]) });
    const si = h("input", { type: "file", accept: ".srt,.vtt,text/plain", class: "input" , onchange: (e) => { subs = e.target.files[0] || null; } });
    const q = h("input", { class: "input", placeholder: "İsteğe bağlı: aranacak anlar (ör. gol, penaltı)" });
    const rights = h("input", { type: "checkbox" });
    const title = h("strong", null, "Videoyu buraya sürükleyin veya seçin");
    const sub = h("span", { class: "muted" }, "MP4, MOV, MKV, WebM — en fazla 2 GB");
    const drop = h("div", { class: "drop", tabindex: 0, role: "button", onclick: () => fi.click(),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") fi.click(); },
      ondragover: (e) => { e.preventDefault(); drop.classList.add("over"); }, ondragleave: () => drop.classList.remove("over"),
      ondrop: (e) => { e.preventDefault(); drop.classList.remove("over"); setFile(e.dataTransfer.files[0]); } }, title, sub);
    function setFile(f) { if (!f) return; file = f; title.textContent = f.name; sub.textContent = (f.size / 1e6).toFixed(1) + " MB"; }
    const bar = h("i"), barw = h("div", { class: "upbar", hidden: true }, bar);
    const btn = h("button", { class: "btn primary lg", type: "submit" }, "Yükle ve klipleri üret");
    return h("form", { onsubmit: (e) => {
      e.preventDefault(); submitErr.hidden = true;
      if (!file) return fail(new Error("Bir video dosyası seçin."));
      if (!rights.checked) return fail(new Error("Videoyu işleme hakkınız olduğunu onaylamalısınız."));
      const fd = new FormData();
      fd.append("video", file); if (subs) fd.append("subs", subs); fd.append("rights", "1"); fd.append("query", q.value.trim());
      Object.entries(payloadBase()).forEach(([k, v]) => fd.append(k, typeof v === "boolean" ? (v ? "1" : "") : v));
      btn.disabled = true; barw.hidden = false;
      const x = new XMLHttpRequest();
      x.open("POST", "/api/jobs/upload"); x.setRequestHeader("X-CSRF-Token", S.csrf);
      x.upload.onprogress = (ev) => { if (ev.lengthComputable) bar.style.width = (ev.loaded / ev.total * 100) + "%"; };
      x.onload = () => {
        let d = {}; try { d = JSON.parse(x.responseText); } catch {}
        if (x.status === 202) go("#/proje/" + d.id); else { fail(new Error(d.error || "Yükleme başarısız.")); btn.disabled = false; barw.hidden = true; }
      };
      x.onerror = () => { fail(new Error("Bağlantı hatası.")); btn.disabled = false; barw.hidden = true; };
      x.send(fd);
    } }, fi, drop, barw,
      h("div", { class: "grid2", style: "margin-top:14px" },
        h("div", null, h("label", { class: "fl" }, "Hazır altyazı (isteğe bağlı .srt / .vtt)"), si),
        h("div", null, h("label", { class: "fl" }, "Aranacak anlar"), q)),
      h("label", { class: "check", style: "margin-top:14px" }, rights, "Bu videoyu işleme hakkına sahip olduğumu (kendi içeriğim veya izinli) onaylıyorum."),
      h("div", { style: "margin-top:16px" }, btn), common(), submitErr);
  };

  const panes = { search: searchPane, url: urlPane, upload: uploadPane };
  const tabsEl = h("div", { class: "tabs" });
  const body = h("div");
  const draw = () => {
    tabsEl.replaceChildren(...[["search", "🔎 Video Araştırmacısı"], ["url", "🔗 YouTube bağlantısı"], ["upload", "⬆ Video yükle"]].map(([k, l]) =>
      h("button", { class: "tab" + (S.mode === k ? " on" : ""), onclick: () => { S.mode = k; draw(); } }, l)));
    body.replaceChildren(panes[S.mode]());
  };
  draw();

  const recent = jobs.slice(0, 6);
  const done = jobs.filter(j => j.status === "done");
  mount(shell("panel",
    h("section", { class: "hero" },
      h("h1", null, "Konuyu yaz, ", h("em", null, "en iyi anları"), " yapay zekâ bulsun."),
      h("p", null, "Lisanslı videoları arar, analiz eder, Türkçe altyazılı 9:16 kısa videolara çevirir. Video yüklemek zorunda değilsiniz.")),
    h("section", { class: "studio" }, tabsEl, body),
    h("section", { class: "stats" },
      stat(jobs.length, "Proje"), stat(done.reduce((a, j) => a + j.clip_count, 0), "Üretilen klip"),
      stat(jobs.filter(j => j.status === "running" || j.status === "queued").length, "Çalışan işlem"),
      h("a", { class: "stat", href: "#/servisler", style: "text-decoration:none;color:inherit" },
        h("b", null, ozet.tam_otomatik_youtube ? "Hazır" : "Eksik var"), h("span", null, "Tam otomatik YouTube hattı → servis durumu"))),
    h("div", { class: "sec-h" }, h("h2", null, "Son projeler"), jobs.length > 6 && h("a", { href: "#/projeler" }, "Tümünü gör →")),
    recent.length ? h("div", { class: "pgrid" }, recent.map(projectCard)) : h("div", { class: "empty" }, "Henüz projeniz yok. Yukarıdan bir konu yazarak başlayın."),
    footer()));
}
const stat = (n, l) => h("div", { class: "stat" }, h("b", null, String(n)), h("span", null, l));
const footer = () => h("footer", { class: "ft" }, "FutbolClip AI yalnızca CC lisanslı, kamu malı veya içerik sahibinin izin verdiği videolarla çalışır. Yayın öncesi kaynak ve lisans koşullarını kontrol etmek size aittir.");

function statusPill(j) {
  const m = { queued: ["Sırada", "info"], running: ["İşleniyor", "info"], done: ["Hazır", "ok"], error: ["Hata", "bad"] }[j.status] || [j.status, ""];
  return h("span", { class: "pill " + m[1] }, (j.status === "running" || j.status === "queued") && h("span", { class: "dot pulse" }), m[0]);
}
function projectCard(j) {
  return h("article", { class: "pcard", tabindex: 0, onclick: () => go("#/proje/" + j.id), onkeydown: (e) => e.key === "Enter" && go("#/proje/" + j.id) },
    h("div", { class: "pthumb", style: j.thumb_url ? `background-image:url('${j.thumb_url}');background-position:center;background-size:cover` : "" }, statusPill(j), !j.thumb_url && "▶"),
    h("div", { class: "pbody" }, h("h3", null, j.title || "Başlıksız"),
      h("div", { class: "muted", style: "font-size:12.5px" }, `${j.kind === "search" ? "Araştırma" : j.kind === "url" ? "Bağlantı" : "Yükleme"} • ${j.clip_count} klip • ${fmtDate(j.created_at)}`)));
}

/* ---------- projeler listesi ---------- */
async function viewProjects() {
  mount(shell("projeler", h("div", { class: "skel", style: "height:300px" })));
  const jobs = await api("/api/jobs");
  mount(shell("projeler", h("div", { class: "ph" }, h("h1", null, "Projelerim")),
    jobs.length ? h("div", { class: "pgrid" }, jobs.map(projectCard)) : h("div", { class: "empty" }, "Henüz proje yok."), footer()));
}

/* ---------- proje sayfası ---------- */
const STAGES = [["arama", "Arama"], ["indirme", "İndirme"], ["analiz", "Ses analizi"], ["transkripsiyon", "Türkçe altyazı"], ["konusmaci", "Konuşmacılar"], ["secim", "Bölüm seçimi"], ["onizleme", "Önizleme"]];

async function viewProject(id) {
  const root = h("div");
  mount(shell("projeler", root));
  let lastKey = "";
  let openMenu = null;
  async function refresh() {
    let j;
    try { j = await api("/api/jobs/" + id); } catch (e) { clearTimers(); root.replaceChildren(h("div", { class: "empty" }, e.status === 404 ? "Proje bulunamadı." : e.message)); return; }
    const active = j.status === "queued" || j.status === "running" || j.clips.some(c => c.status === "rendering");
    const key = JSON.stringify([j.status, j.stage, j.progress, j.message, j.warnings, j.clips.map(c => [c.id, c.status, c.preview_url, c.title])]);
    if (key !== lastKey) { lastKey = key; render(j); }
    if (!active) clearTimers();
  }
  function render(j) {
    const running = j.status === "queued" || j.status === "running";
    const idx = STAGES.findIndex(s => s[0] === j.stage);
    fill(root,
      h("div", { class: "crumb" }, h("a", { href: "#/projeler" }, "Projeler"), "›", h("span", null, j.title)),
      h("div", { class: "ph" },
        h("div", null, h("h1", null, j.title), h("div", { class: "row", style: "margin-top:10px" }, statusPill(j),
          h("span", { class: "muted" }, `${j.kind === "search" ? "Video Araştırmacısı" : j.kind === "url" ? "YouTube bağlantısı" : "Yüklenen video"} • ${fmtDate(j.created_at)}`))),
        !running && h("button", { class: "btn danger", onclick: async () => {
          if (!confirm("Bu proje ve tüm klipleri silinsin mi?")) return;
          try { await api("/api/jobs/" + j.id, { method: "DELETE" }); go("#/projeler"); } catch (e) { toast(e.message, "bad"); } } }, "Projeyi sil")),
      running && h("div", { class: "card prog" },
        h("div", { class: "row", style: "justify-content:space-between;margin-bottom:10px" }, h("b", null, j.message || "İşleniyor…"), h("span", { class: "mono muted" }, Math.round(j.progress) + "%")),
        h("div", { class: "prog-bar" }, h("i", { style: `width:${j.progress}%` })),
        h("div", { class: "steps" }, STAGES.map(([k, l], i) => h("span", { class: "step" + (i < idx ? " done" : i === idx ? " now" : "") }, (i < idx ? "✓ " : "") + l))),
        h("p", { class: "muted", style: "margin:10px 0 0;font-size:13px" }, "Bu sayfadan ayrılabilirsiniz; işlem arka planda sürer.")),
      j.status === "error" && h("div", { class: "notice bad", style: "margin-bottom:18px" }, h("span", null, "✕"), h("span", null, h("b", null, "İşlem tamamlanamadı. "), j.error, " ", h("a", { href: "#/servisler" }, "Servis durumu"))),
      j.warnings.length > 0 && h("div", { class: "warns" }, j.warnings.map(w => h("div", { class: "notice warn", style: "margin:0" }, h("span", null, "⚠"), h("span", null, w)))),
      j.videos && j.videos.length > 0 && h("div", null, h("h2", { style: "font-size:16px;margin:6px 0 12px" }, "Kullanılan kaynak videolar"),
        h("div", { class: "vsrc" }, j.videos.map(v => h("div", { class: "vs" },
          v.thumbnail ? h("img", { src: v.thumbnail, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }) : h("img", { alt: "" }),
          h("div", null, h("b", { title: v.title }, v.title), h("span", { class: "muted" }, [v.channel, v.license].filter(Boolean).join(" • ")), h("br"),
            v.speakers ? h("span", { class: "faint" }, `${v.speakers} konuşmacı • ${v.provider || ""}`) : null,
            v.url ? h("div", null, h("a", { href: v.url, target: "_blank", rel: "noopener noreferrer" }, "Kaynağı aç ↗")) : null))))),
      j.clips.length > 0 && h("div", { class: "sec-h" }, h("h2", null, `${j.clips.length} kısa video önerisi`), h("span", { class: "muted", style: "font-size:13px" }, "Viral puanına göre sıralı")),
      h("div", { class: "cgrid" }, j.clips.map(clipCard)),
      !running && j.status === "done" && j.clips.length === 0 && h("div", { class: "empty" }, "Uygun klip bulunamadı."),
      footer());
  }
  function clipCard(c) {
    const busy = c.status === "rendering";
    const media = h("div", { class: "cv" },
      c.thumb_url && !busy ? h("img", { src: c.thumb_url, alt: c.title, loading: "lazy" }) : null,
      busy && h("div", { class: "busy" }, h("div", null, h("div", { class: "spin" }), "Önizleme hazırlanıyor")),
      c.status === "error" && h("div", { class: "busy" }, "Önizleme hatası: " + (c.error || "")),
      !busy && c.preview_url && h("button", { class: "play", "aria-label": "Önizlemeyi oynat", onclick: (e) => {
        const v = h("video", { src: c.preview_url, controls: true, autoplay: true, playsinline: true });
        e.currentTarget.parentNode.replaceChildren(v, ...[...media.querySelectorAll(".score,.dur")]); v.play().catch(() => {}); } }, h("span", { html: SVG.play })),
      h("span", { class: "score", title: "Viral puanı" }, "🔥 ", h("b", null, Math.round(c.score))), h("span", { class: "dur" }, fmtT(c.duration) ));
    const exp = h("div", { class: "menu" });
    const mkExp = () => {
      exp.replaceChildren(h("button", { class: "btn primary sm", style: "width:100%", disabled: c.status !== "ready", onclick: (e) => {
        e.stopPropagation(); closeMenu();
        const pop = h("div", { class: "pop" }, h("small", null, "MP4 formatı seçin"),
          ...Object.entries(S.status ? S.status.platforms : { tiktok: { name: "TikTok" }, reels: { name: "Instagram Reels" }, shorts: { name: "YouTube Shorts" } }).map(([k, v]) =>
            h("button", { onclick: () => { closeMenu(); startExport(c, k); } }, svgEl(SVG.dl), v.name + " (1080×1920)")));
        exp.append(pop); openMenu = pop; } }, svgEl(SVG.dl), "MP4"));
    };
    mkExp();
    return h("article", { class: "clip" }, media,
      h("div", { class: "cb" }, h("h3", null, c.title),
        h("div", { class: "tags" }, (c.tags || []).slice(0, 4).map(t => h("span", { class: "pill" }, t)), c.speakers.length > 1 && h("span", { class: "pill info" }, c.speakers.length + " konuşmacı")),
        c.reason && h("p", { class: "why", style: "margin:0" }, c.reason),
        h("span", { class: "faint mono", style: "font-size:11.5px" }, `${fmtT(c.start)}–${fmtT(c.end)} kaynak`)),
      h("div", { class: "ca" }, h("button", { class: "btn sm", disabled: busy, onclick: () => openEditor(c.id, refresh) }, svgEl(SVG.edit), "Düzenle"), exp));
  }
  function closeMenu() { if (openMenu) { openMenu.remove(); openMenu = null; } }
  document.addEventListener("click", closeMenu);
  await refresh();
  every(refresh, 2000);
}

/* ---------- dışa aktarma (dock) ---------- */
async function startExport(c, platform) {
  try {
    const e = await api(`/api/clips/${c.id}/export`, { method: "POST", body: { platform } });
    trackExport(e.id, c.title, e.platform_name);
  } catch (ex) { toast(ex.message, "bad"); }
}
function trackExport(id, title, pname) {
  const dock = document.getElementById("dock");
  const bar = h("i", { style: "width:0%" }), line = h("span", { class: "muted" }, "Sırada…");
  const box = h("div", { class: "dk" }, h("b", null, title.slice(0, 48)), h("div", null, pname, " • ", line), h("div", { class: "mini" }, bar));
  dock.append(box);
  const t = setInterval(async () => {
    try {
      const e = await api("/api/exports/" + id);
      bar.style.width = e.progress + "%"; line.textContent = e.status === "rendering" ? `MP4 hazırlanıyor %${Math.round(e.progress)}` : "Sırada…";
      if (e.status === "ready") {
        clearInterval(t); box.replaceChildren(h("b", null, "✓ MP4 hazır"), h("div", { class: "muted" }, `${title.slice(0, 40)} • ${fmtSize(e.size)}`),
          h("div", { class: "row", style: "margin-top:10px" }, h("a", { class: "btn primary sm", href: e.download_url, download: "" }, svgEl(SVG.dl), "İndir"),
            h("button", { class: "btn sm ghost", onclick: () => box.remove() }, "Kapat")));
        const a = h("a", { href: e.download_url, download: "" }); document.body.append(a); a.click(); a.remove();
      } else if (e.status === "error") {
        clearInterval(t); box.replaceChildren(h("b", { style: "color:var(--red)" }, "Dışa aktarma hatası"), h("div", { class: "muted" }, e.error || ""),
          h("button", { class: "btn sm ghost", onclick: () => box.remove() }, "Kapat"));
      }
    } catch { clearInterval(t); box.remove(); }
  }, 1500);
}

/* ---------- klip düzenleyici ---------- */
async function openEditor(cid, onClose) {
  let c = await api("/api/clips/" + cid);
  const st = S.status || await api("/api/status"); S.status = st;
  const origStart = c.start, origEnd = c.end;
  const vdur = c.source.duration || Math.max(c.end + 30, 120);
  let capsDirty = false, caps = JSON.parse(JSON.stringify(c.captions || []));
  const m = { start: c.start, end: c.end, layout: c.layout, platform: c.platform, title: c.title, style: { ...c.style } };

  const modal = h("div", { class: "modal", role: "dialog", "aria-modal": "true", onclick: (e) => e.target === modal && close() });
  const pv = h("div", { class: "pv" }), msg = h("div", { class: "notice", hidden: true });
  const tl = h("i"), durLbl = h("b"), warnLbl = h("span", { class: "pill" });
  const inS = h("input", { class: "input", type: "number", step: "0.1", min: 0 }), inE = h("input", { class: "input", type: "number", step: "0.1", min: 0 });
  function drawTl() {
    tl.style.left = (m.start / vdur * 100) + "%"; tl.style.width = Math.max(1, (m.end - m.start) / vdur * 100) + "%";
    const d = m.end - m.start; durLbl.textContent = d.toFixed(1) + " sn";
    const ok = d >= 15 && d <= 60, bad = d < 5 || d > 180;
    warnLbl.className = "pill " + (bad ? "bad" : ok ? "ok" : "warn"); warnLbl.textContent = bad ? "5–180 sn olmalı" : ok ? "İdeal süre" : "Önerilen 15–60 sn";
    inS.value = m.start.toFixed(1); inE.value = m.end.toFixed(1);
  }
  const num = (inp, key, lbl) => {
    const set = (v) => { m[key] = Math.min(vdur, Math.max(0, Math.round(v * 10) / 10)); drawTl(); };
    inp.onchange = () => set(parseFloat(inp.value) || 0);
    return h("div", null, h("label", { class: "fl" }, lbl), h("div", { class: "nudge" },
      h("button", { class: "btn sm", type: "button", onclick: () => set(m[key] - 1) }, "−1"), inp, h("button", { class: "btn sm", type: "button", onclick: () => set(m[key] + 1) }, "+1")));
  };
  function drawPv() {
    const busy = c.status === "rendering";
    pv.replaceChildren(h("div", { class: "cv" }, busy ? h("div", { class: "busy" }, h("div", null, h("div", { class: "spin" }), "Önizleme yenileniyor…"))
      : c.preview_url ? h("video", { src: c.preview_url, controls: true, playsinline: true, poster: c.thumb_url || null }) : h("div", { class: "busy" }, "Önizleme yok")));
  }
  const title = h("input", { class: "input", value: m.title, maxlength: 120, oninput: (e) => { m.title = e.target.value; } });
  const layout = h("select", { class: "input", onchange: (e) => { m.layout = e.target.value; } }, Object.entries(st.layouts).map(([k, v]) => h("option", { value: k, selected: m.layout === k }, v)));
  const plat = h("select", { class: "input", onchange: (e) => { m.platform = e.target.value; } }, Object.entries(st.platforms).map(([k, v]) => h("option", { value: k, selected: m.platform === k }, v.name)));
  const swWrap = h("div", { class: "swatches" });
  const drawSw = () => swWrap.replaceChildren(...Object.entries(st.colors).map(([k, v]) => h("button", { type: "button", class: "sw" + (m.style.color === k ? " on" : ""), title: v.name, "aria-label": v.name,
    style: `background:${v.hex}`, onclick: () => { m.style.color = k; drawSw(); } })));
  drawSw();
  const fs = h("input", { class: "range", type: "range", min: 40, max: 110, value: m.style.font_size || 74, oninput: (e) => { m.style.font_size = +e.target.value; fsv.textContent = e.target.value; } });
  const fsv = h("b", null, String(m.style.font_size || 74));
  const tog = (key, label) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!m.style[key], onchange: (e) => { m.style[key] = e.target.checked; } }), label);
  const capsEl = h("div", { class: "caps" });
  const drawCaps = () => capsEl.replaceChildren(...(caps.length ? caps.map((cp, i) => h("div", { class: "cap" },
    h("time", null, `${fmtT(cp.start)}–${fmtT(cp.end)}`),
    h("textarea", { class: "input", rows: 1, value: cp.text, oninput: (e) => { caps[i].text = e.target.value; capsDirty = true; } }))) : [h("p", { class: "muted" }, "Bu klipte altyazı yok.")]));
  drawCaps();

  const save = h("button", { class: "btn primary", onclick: apply }, "Değişiklikleri uygula");
  async function apply() {
    msg.hidden = true; save.disabled = true;
    const timesChanged = m.start !== origStart || m.end !== origEnd;
    const body = { start: m.start, end: m.end, title: m.title, layout: m.layout, platform: m.platform, style: m.style };
    if (capsDirty && !timesChanged) body.captions = caps;
    if (capsDirty && timesChanged) toast("Süre değiştiği için altyazılar yeniden üretilecek; elle yaptığınız metin düzeltmeleri uygulanmayacak.", "warn");
    try {
      c = await api("/api/clips/" + cid, { method: "PATCH", body });
      capsDirty = false; drawPv();
      const t = setInterval(async () => {
        try { c = await api("/api/clips/" + cid); } catch { clearInterval(t); return; }
        if (c.status !== "rendering") { clearInterval(t); save.disabled = false; caps = JSON.parse(JSON.stringify(c.captions || [])); drawCaps();
          if (c.status === "error") { msg.className = "notice bad"; msg.textContent = "Önizleme hatası: " + c.error; msg.hidden = false; } else toast("Önizleme güncellendi."); }
        drawPv();
      }, 1500);
    } catch (e) { msg.className = "notice bad"; msg.textContent = e.message; msg.hidden = false; save.disabled = false; }
  }
  const expMenu = h("div", { class: "menu" }, h("button", { class: "btn", onclick: (e) => { e.stopPropagation();
    const ex = expMenu.querySelector(".pop"); if (ex) return ex.remove();
    if (c.status === "rendering") return toast("Önizleme yenilenirken bekleyin, bitince dışa aktarabilirsiniz.", "warn");
    expMenu.append(h("div", { class: "pop" }, h("small", null, "MP4 olarak dışa aktar"),
      ...Object.entries(st.platforms).map(([k, v]) => h("button", { onclick: () => { expMenu.querySelector(".pop")?.remove(); startExport(c, k); } }, svgEl(SVG.dl), v.name)))); } },
    svgEl(SVG.dl), "MP4 dışa aktar"));

  function close() { modal.remove(); document.removeEventListener("keydown", esc); onClose && onClose(); }
  const esc = (e) => e.key === "Escape" && close();
  document.addEventListener("keydown", esc);
  drawPv(); drawTl();
  modal.append(h("div", { class: "sheet" },
    h("div", { class: "sheet-h" }, h("h3", null, "Klibi düzenle"), h("button", { class: "btn ghost sm", onclick: close, "aria-label": "Kapat" }, svgEl(SVG.x))),
    h("div", { class: "sheet-b" }, pv, h("div", { class: "ctl" },
      h("div", null, h("label", { class: "fl" }, "Başlık"), title),
      h("div", null, h("h4", null, "Kırpma (kaynak videodaki saniye)"),
        h("div", { class: "tl", title: "Seçili bölüm" }, tl), h("div", { class: "row", style: "margin-top:8px;justify-content:space-between" }, h("span", { class: "muted" }, "Süre: ", durLbl), warnLbl),
        h("div", { class: "trim" }, num(inS, "start", "Başlangıç"), num(inE, "end", "Bitiş")),
        h("p", { class: "faint", style: "font-size:12px;margin:8px 0 0" }, `Kaynak video süresi: ${fmtT(vdur)}. Süre değişirse altyazılar yeniden eşleştirilir.`)),
      h("div", { class: "grid2" }, h("div", null, h("label", { class: "fl" }, "Dikey format"), layout), h("div", null, h("label", { class: "fl" }, "Platform"), plat)),
      h("div", null, h("h4", null, "Altyazı stili"),
        h("div", { class: "grid2" }, h("div", null, h("label", { class: "fl" }, "Vurgu rengi"), swWrap), h("div", null, h("label", { class: "fl" }, "Yazı boyutu: ", fsv), fs)),
        h("div", { class: "row", style: "margin-top:12px;gap:20px" }, tog("captions", "Altyazı göster"), tog("uppercase", "BÜYÜK HARF"), tog("speaker_labels", "Konuşmacı etiketi"), tog("credit", "Kaynak kredisi"))),
      h("div", null, h("h4", null, "Altyazı metni (Türkçe)"), capsEl),
      msg)),
    h("div", { class: "sheet-f" }, h("span", { class: "muted", style: "margin-right:auto;font-size:13px" }, `Kaynak: ${c.source.channel || c.source.title || "yükleme"} • ${c.source.license || ""}`), save, expMenu)));
  document.body.append(modal);
  document.addEventListener("click", () => expMenu.querySelector(".pop")?.remove());
}

/* ---------- dışa aktarımlar ---------- */
async function viewExports() {
  const root = h("div"); mount(shell("exports", root));
  async function load() {
    const list = await api("/api/exports");
    fill(root, h("div", { class: "ph" }, h("h1", null, "Dışa Aktarımlar")),
      list.length ? h("div", { class: "card", style: "padding:0;overflow:hidden" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["Klip", "Platform", "Boyut", "Durum", ""].map(x => h("th", null, x)))),
        h("tbody", null, list.map(e => h("tr", null,
          h("td", null, h("b", null, e.title || "Klip"), h("div", { class: "faint", style: "font-size:12px" }, fmtDate(e.created_at) + (e.duration ? ` • ${e.duration} sn` : ""))),
          h("td", null, e.platform_name), h("td", { class: "mono" }, fmtSize(e.size)),
          h("td", null, e.status === "ready" ? h("span", { class: "pill ok" }, "Hazır") : e.status === "error" ? h("span", { class: "pill bad", title: e.error }, "Hata")
            : h("div", null, h("span", { class: "pill info" }, "İşleniyor %" + Math.round(e.progress)), h("div", { class: "mini", style: "margin-top:6px" }, h("i", { style: `width:${e.progress}%` })))),
          h("td", { style: "text-align:right;white-space:nowrap" }, e.download_url && h("a", { class: "btn sm primary", href: e.download_url, download: "" }, svgEl(SVG.dl), "İndir"), " ",
            h("button", { class: "btn sm ghost danger", onclick: async () => { await api("/api/exports/" + e.id, { method: "DELETE" }); load(); } }, "Sil"))))))) :
      h("div", { class: "empty" }, "Henüz dışa aktarılmış video yok. Bir projeden klip seçip «MP4» düğmesini kullanın."), footer());
    if (!list.some(e => e.status === "queued" || e.status === "rendering")) clearTimers();
  }
  await load(); every(load, 2500);
}

/* ---------- servis durumu ---------- */
async function viewServices() {
  mount(shell("servisler", h("div", { class: "skel", style: "height:300px" })));
  const st = await api("/api/status"); S.status = st;
  const groups = {}; st.hizmetler.forEach(x => (groups[x.grup] ||= []).push(x));
  const lab = { hazir: ["Hazır", "ok"], eksik: ["Eksik (zorunlu)", "bad"], istege_bagli: ["Kurulu değil", "warn"], desteklenmiyor: ["Desteklenmiyor", ""] };
  const o = st.ozet;
  mount(shell("servisler",
    h("div", { class: "ph" }, h("div", null, h("h1", null, "Servis Durumu"), h("p", { class: "muted", style: "margin:8px 0 0;max-width:62ch" }, "FutbolClip AI’nin hangi bileşenlerinin bu sunucuda çalıştığını ve eksikleri nasıl tamamlayacağınızı gösterir."))),
    h("div", { class: "notice" + (o.tam_otomatik_youtube ? "" : " warn"), style: "margin:0 0 28px" }, h("span", null, o.tam_otomatik_youtube ? "✓" : "⚠"),
      h("span", null, h("b", null, o.tam_otomatik_youtube ? "Tam otomatik YouTube hattı hazır." : "Tam otomatik YouTube hattı için eksikler var: "),
        !o.tam_otomatik_youtube && [!o.youtube_arama && " YouTube API anahtarı;", !o.youtube_indirme && " yt-dlp;", !o.transkripsiyon && " konuşma tanıma servisi (faster-whisper / Groq / OpenAI)."].filter(Boolean).join("")
        , " Kendi videonuzu yükleyip SRT ekleyerek bu servisler olmadan da çalışabilirsiniz.")),
    ...Object.entries(groups).map(([g, items]) => h("section", { class: "svc-g" }, h("h3", null, g),
      items.map(x => h("div", { class: "svc" }, h("div", null, h("b", null, x.ad), h("p", null, x.aciklama), x.nasil && x.durum !== "hazir" && h("p", null, "Nasıl: ", h("code", null, x.nasil))),
        h("span", { class: "pill " + lab[x.durum][1], style: "align-self:start" }, lab[x.durum][0]))))),
    footer()));
}

/* ---------- yönlendirme ---------- */
async function route() {
  clearTimers();
  document.getElementById("dock").querySelectorAll(".dk").length;
  const hash = location.hash || "#/";
  try {
    if (!S.user) { return viewAuth(hash === "#/kayit" && S.regOpen ? "register" : "login"); }
    if (hash === "#/giris" || hash === "#/kayit") return go("#/");
    const m = hash.match(/^#\/proje\/([\w-]+)/);
    if (m) return await viewProject(m[1]);
    if (hash === "#/projeler") return await viewProjects();
    if (hash === "#/disa-aktarimlar") return await viewExports();
    if (hash === "#/servisler") return await viewServices();
    return await viewPanel();
  } catch (e) { toast(e.message, "bad"); }
}
async function boot() {
  const me = await api("/api/me"); S.csrf = me.csrf; S.user = me.user; S.regOpen = me.registration_open;
}
window.addEventListener("hashchange", () => { if (!S.user && !/^#\/(giris|kayit)?$/.test(location.hash)) location.hash = "#/giris"; else route(); });
boot().then(route).catch(e => { app.textContent = "Sunucuya ulaşılamıyor: " + e.message; });
