"use strict";

// Fork v69: upstream Tagcast 1.3.0's Mini Player for the browser. Upstream opens it as an Electron
// window; here it opens as an always-on-top Picture-in-Picture window (Chrome / Edge 116+), else a
// small popup. Same contract as upstream's preload (tagcastMiniOwner / tagcastMini): the Mini Player
// page has no audio of its own, it only shows snapshots and sends commands back to this tab.
(() => {
  if (window.tagcastMiniOwner) return;  // the desktop app brings its own
  const owner = {onActiveHandler: null, onCommandHandler: null, view: null, pip: false, serial: 0, pending: new Map(), state: null};

  function setActive(active) { owner.onActiveHandler?.(active); }
  function closed() {
    owner.view = null; owner.state = null;
    for (const resolve of owner.pending.values()) resolve(false);
    owner.pending.clear(); setActive(false);
  }

  function bridgeFor(view) {
    return {
      command(command) {
        return new Promise(resolve => {
          const id = ++owner.serial; owner.pending.set(id, resolve);
          try { owner.onCommandHandler?.({id, command}); } catch { owner.pending.delete(id); resolve(false); }
        });
      },
      openMain() { try { window.focus(); } catch { /* the browser decides */ } },
      pin() { /* Picture-in-Picture is always on top; a popup cannot be pinned */ },
      onState(handler) { owner.state = handler; },
      ready() { owner.view = view; setActive(true); }
    };
  }
  window.tagcastMiniBridge = view => bridgeFor(view);  // a popup asks through window.opener

  async function openPictureInPicture() {
    const pip = await documentPictureInPicture.requestWindow({width: 400, height: 240})  /* fork v85: room for a lyric line */;
    const page = await fetch("mini-player/index.html").then(response => response.text());
    const parsed = new DOMParser().parseFromString(page, "text/html"), base = new URL("mini-player/", location.href);
    pip.document.documentElement.lang = "en";
    pip.document.title = parsed.title;
    for (const link of parsed.querySelectorAll('link[rel="stylesheet"]')) {
      const node = pip.document.createElement("link"); node.rel = "stylesheet"; node.href = new URL(link.getAttribute("href"), base).href;
      pip.document.head.append(node);
    }
    const meta = pip.document.createElement("meta"); meta.name = "referrer"; meta.content = "no-referrer"; pip.document.head.append(meta);
    pip.document.body.className = parsed.body.className;
    for (const node of [...parsed.body.childNodes]) pip.document.body.append(pip.document.importNode(node, true));
    pip.tagcastMini = bridgeFor(pip);
    owner.pip = true;
    pip.addEventListener("pagehide", closed, {once: true});
    const script = pip.document.createElement("script"); script.src = new URL("view.js", base).href;
    pip.document.body.append(script);
  }

  function openPopup() {
    const popup = window.open("mini-player/index.html", "tagcast-mini-player", "popup,width=420,height=280");
    if (!popup) { if (typeof toast === "function") toast("Allow pop-ups for Tagcast to open the Mini Player."); return; }
    owner.pip = false;
    const watch = setInterval(() => { if (popup.closed) { clearInterval(watch); if (owner.view === popup) closed(); } }, 1000);
  }

  window.tagcastMiniOwner = {
    async open() {
      if (owner.view && !owner.view.closed) { try { owner.view.focus(); } catch { /* focus is best effort */ } return; }
      try {
        if (window.documentPictureInPicture?.requestWindow) await openPictureInPicture(); else openPopup();
      } catch { openPopup(); }
    },
    publish(snapshot) { owner.state?.({playback: snapshot, pinned: owner.pip, canPin: false}); },
    onActive(handler) { owner.onActiveHandler = handler; },
    onCommand(handler) { owner.onCommandHandler = handler; },
    respond({id, ok}) { const resolve = owner.pending.get(id); owner.pending.delete(id); resolve?.(ok === true); },
    ready() { /* nothing to announce in a browser */ }
  };
})();
