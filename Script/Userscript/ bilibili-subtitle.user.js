// ==UserScript==
// @name        Bilibili Subtitle Extractor
// @description Subtitle extraction for Bilibili videos
// @namespace   https://github.com/dearealeo
// @match       https://www.bilibili.com/video/*
// @version     2.0.0
// @author      dearealeo <https://github.com/dearealeo>
// @grant       none
// ==/UserScript==

(() => {
  "use strict";
  const S = {
      c: "#subtitle-download-container",
      i: "#subtitle-download-icon",
      t: "#subtitle-download-text",
      l: "#loading-dot",
      s: "#subtitle-loading-style",
    },
    E = {
      p: "._Part_1iu0q_16",
      tm: "._TimeText_1iu0q_35",
      tx: "._Text_1iu0q_64",
      lb: "._Label_krx6h_18",
      ai: ".video-ai-assistant",
      cl: ".close-btn",
    },
    R = /^\d+:\d+$/,
    D = new WeakMap(),
    F = new Set();
  let B, T, I, L, N;
  const O = new MutationObserver((m, o) => {
    document.readyState === "complete" && !B && (C(), o.disconnect());
  });
  O.observe(document, { childList: !0, subtree: !0 });
  document.readyState === "complete" && !B && C();
  function C() {
    if ((B = document.querySelector(S.c))) return;
    const f = document.createDocumentFragment(),
      c = document.createElement("div");
    c.id = S.c.slice(1);
    Object.assign(c.style, {
      position: "fixed",
      left: "0",
      top: "50%",
      transform: "translateY(-50%)",
      backgroundColor: "rgba(251,114,153,.7)",
      color: "white",
      padding: "5px 8px",
      borderRadius: "0 4px 4px 0",
      cursor: "pointer",
      zIndex: "999",
      display: "flex",
      alignItems: "center",
      boxShadow: "2px 2px 10px rgba(0,0,0,.2)",
      transition: "all .3s ease",
      fontSize: "12px",
    });
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("width", "10");
    s.setAttribute("height", "10");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("fill", "none");
    s.style.marginRight = "4px";
    s.id = S.i.slice(1);
    s.innerHTML =
      '<path fill-rule="evenodd" clip-rule="evenodd" d="M12 4C12.5523 4 13 4.44772 13 5V13.5858L15.2929 11.2929C15.6834 10.9024 16.3166 10.9024 16.7071 11.2929C17.0976 11.6834 17.0976 12.3166 16.7071 12.7071L12.7071 16.7071C12.3166 17.0976 11.6834 17.0976 11.2929 16.7071L7.29289 12.7071C6.90237 12.3166 6.90237 11.6834 7.29289 11.2929C7.68342 10.9024 8.31658 10.9024 8.70711 11.2929L11 13.5858V5C11 4.44772 11.4477 4 12 4ZM4 14C4.55228 14 5 14.4477 5 15V17C5 17.5523 5.44772 18 6 18H18C18.5523 18 19 17.5523 19 17V15C19 14.4477 19.4477 14 20 14C20.5523 14 21 14.4477 21 15V17C21 18.6569 19.6569 20 18 20H6C4.34315 20 3 18.6569 3 17V15C3 14.4477 3.44772 14 4 14Z" fill="white"/>';
    T = document.createElement("span");
    T.textContent = "Extract";
    T.style.fontSize = "12px";
    T.id = S.t.slice(1);
    c.appendChild(s);
    c.appendChild(T);
    c.addEventListener("click", P, { passive: !0 });
    f.appendChild(c);
    document.body.appendChild(f);
    B = c;
  }
  function P() {
    if (!B || !T) return;
    const x = T.textContent,
      y = B.style.backgroundColor;
    T.textContent = "Processing";
    B.style.backgroundColor = "rgba(251,114,153,.9)";
    if (!I) {
      I = document.createElement("style");
      I.id = S.s.slice(1);
      I.textContent =
        "@keyframes p{0%{opacity:.2}50%{opacity:1}100%{opacity:.2}}";
      document.head.appendChild(I);
    }
    if (!L) {
      L = document.createElement("span");
      L.textContent = " •";
      L.style.animation = "p 1s infinite";
      L.id = S.l.slice(1);
      T.appendChild(L);
    }
    const a = document.querySelector(E.ai);
    if (!a) {
      U("AI assistant unavailable");
      return;
    }
    a.click();
    setTimeout(() => {
      const b = A();
      if (!b) {
        U("Subtitle panel inaccessible");
        return;
      }
      b.click();
      setTimeout(() => {
        const r = H() || G();
        if (r.length === 0) {
          U("No subtitle data found");
          return;
        }
        const u = [...new Set(r)];
        navigator.clipboard
          .writeText(u.join("\n"))
          .then(() => {
            V(`Extracted ${u.length} lines`, B);
            W(null, y, x);
          })
          .catch(() => U("Clipboard access denied"));
      }, 1500);
    }, 1500);
    setTimeout(() => {
      const b = document.querySelector(E.cl);
      b && b.click();
    }, 4500);
  }
  function H() {
    const r = [];
    document.querySelectorAll(E.p).forEach((e) => {
      const t = e.querySelector(E.tm),
        s = e.querySelector(E.tx);
      t && s && r.push(`${t.textContent}:${s.textContent}`);
    });
    return r;
  }
  function G() {
    const r = [];
    let e = document.querySelectorAll('[class*="time"],[class*="Time"]');
    e.forEach((t) => {
      if (R.test(t.textContent.trim())) {
        const s = t.nextElementSibling;
        s && r.push(`${t.textContent}:${s.textContent}`);
      }
    });
    if (r.length) return r;
    e = document.querySelectorAll(
      '[class*="subtitle"],[class*="Part"],[class*="Line"]'
    );
    e.forEach((t) => {
      const s = t.children;
      if (s.length >= 2) {
        const n = s[0],
          l = s[1];
        n &&
          R.test(n.textContent.trim()) &&
          r.push(`${n.textContent}:${l.textContent}`);
      }
    });
    if (r.length) return r;
    e = document.querySelectorAll("span");
    for (let t = 0; t < e.length - 1; t++)
      R.test(e[t].textContent.trim()) &&
        r.push(`${e[t].textContent}:${e[t + 1].textContent}`);
    return r.length
      ? r
      : (document
          .querySelectorAll('[class*="text"],[class*="content"]')
          .forEach((t) => {
            const s = t.textContent.trim();
            s.length && r.push(s);
          }),
        r);
  }
  function A() {
    let r = document.querySelector(E.lb);
    if (r?.textContent === "字幕列表") return r;
    const e = document.querySelectorAll("span,button,div");
    for (let t of e) if (t.textContent === "字幕列表") return t;
    const s = document.querySelectorAll('[class*="Label"],[class*="btn"]');
    for (let t of s) if (t.textContent === "字幕列表") return t;
    return null;
  }
  function V(r, e) {
    if (N) return;
    const t = e.getBoundingClientRect();
    N = document.createElement("div");
    N.textContent = r;
    Object.assign(N.style, {
      position: "fixed",
      top: `${t.top}px`,
      left: `${t.right + 10}px`,
      padding: "5px 10px",
      backgroundColor: "#fb7299",
      color: "white",
      borderRadius: "4px",
      zIndex: "9999",
      fontSize: "12px",
      boxShadow: "2px 2px 10px rgba(0,0,0,.2)",
      whiteSpace: "nowrap",
    });
    document.body.appendChild(N);
    setTimeout(() => {
      N && N.parentNode && (document.body.removeChild(N), (N = null));
    }, 1500);
  }
  function W(r, e, t) {
    r && alert(r);
    e && (B.style.backgroundColor = e);
    t && (T.textContent = t);
    L && L.parentNode && (L.parentNode.removeChild(L), (L = null));
  }
  function U(r) {
    W(r);
    const e = document.querySelector(E.cl);
    e && e.click();
  }
})();
