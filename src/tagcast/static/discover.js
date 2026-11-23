"use strict";

// fork v115: ported from upstream Tagcast 1.9.x. The fork has no translation layer, so the strings are plain English here.
const DISCOVER_TEXT = {
  "discover.action": "Similar in your library",
  "discover.fallback": "Last.fm found no matching albums in your library. Showing local suggestions based on genres.",
  "discover.fallbackListenBrainz": "ListenBrainz could not provide matching albums. Showing local suggestions based on genres.",
  "discover.intro": "Albums from other artists in your library, chosen by shared genres. Ratings break ties.",
  "discover.introLastfm": "Albums in your library by artists related to {{artist}} on Last.fm. Ratings choose an album for each artist.",
  "discover.introListenBrainz": "Albums in your library by artists related to {{artist}} on ListenBrainz. Ratings choose an album for each artist.",
  "discover.lastfmSetup": "Enable Last.fm and add its API key in Settings → Sources & keys.",
  "discover.license": "Last.fm API terms",
  "discover.loading": "Finding albums in your library…",
  "discover.missingGenres": "This artist has no usable genres yet. Add genres to their albums to get local suggestions.",
  "discover.noMatches": "No albums by other artists share these genres in your library.",
  "discover.playbackUnavailable": "Open the album to check its tracks.",
  "discover.powered": "Powered by AudioScrobbler",
  "discover.privacyLastfm": "Last.fm receives the selected artist name when you look up suggestions. It uses your API key. Your library and listening history stay local.",
  "discover.privacyListenBrainz": "ListenBrainz needs no account or key. When you look up suggestions, MusicBrainz receives the selected artist name and ListenBrainz receives the matching public artist ID.",
  "discover.refresh": "Refresh suggestions",
  "discover.relatedArtist": "Related artist on Last.fm: {{artist}}",
  "discover.relatedListenBrainz": "Related artist on ListenBrainz: {{artist}}",
  "discover.sharedGenres": "Shared genres: {{genres}}",
  "discover.yourLibrary": "Your library",
  "settings.discover.heading": "Similar music",
  "settings.discover.local": "Local genres",
  "settings.discover.note": "Choose where Tagcast finds related artists. Suggestions always come from albums in your library.",
  "settings.discover.requirements": "ListenBrainz falls back to local genres if it cannot find matching albums. Last.fm requires its source switch and API key in Sources & keys. Searches start when you open Similar in your library or choose Refresh suggestions.",
  "settings.discover.source": "Suggestion source",
  "library.open.album": "Open album {{name}}",
  "discover.invalid_response": "Tagcast got an unexpected answer. Try again."
};
const discoverText = (key, vars = {}) => DISCOVER_TEXT[key].replace(/\{\{(\w+)\}\}/g, (_, name) => String(vars[name] ?? ""));
const discoverHtml = (key, vars = {}) => escapeHtml(discoverText(key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)]))));

// Discovery starts only on a click, using the saved preference rather than a page selector.
const artistDiscover = {albums: null, details: null, session: null, entries: new Map(), request: 0, settings: null};

function discoverSource() { return lookup.discoverSource; }

function discoverSourceLabel(source) {
  if (source === "listenbrainz") return "ListenBrainz";
  if (source === "lastfm") return "Last.fm";
  return discoverText("discover.yourLibrary");
}

function applyDiscoverSettings(data) {
  const legacy = data.discover_lastfm;
  const source = ["local", "listenbrainz", "lastfm"].includes(data.discover_source)
    ? data.discover_source : typeof legacy === "boolean" ? (legacy ? "lastfm" : "local") : "listenbrainz";
  if (source === lookup.discoverSource) return;
  lookup.discoverSource = source;
  resetArtistDiscover();
  if (state.screen === "albums" && state.artist) renderArtistDiscover();
}

function resetArtistDiscover() {
  artistDiscover.request += 1;
  artistDiscover.entries.clear();
  artistDiscover.albums = state.albums; artistDiscover.details = state.details;
  artistDiscover.session = state.connection.playback_session;
  artistDiscover.settings = lookup.settingsRevision;
  $("#artist-discover").hidden = true;
  $("#artist-discover").open = false;
  $("#artist-discover-cards").innerHTML = "";
}

function discoverSelection() {
  if (!live() || state.loading || state.screen !== "albums" || !state.artist) return null;
  const selection = artistProfileSelection(state.artist);
  return selection?.artists.length <= 100 && selection.session ? selection : null;
}

function discoverEntryKey(selection) { return JSON.stringify([discoverSource(), selection.name]); }
function discoverExpired(entry) { return entry?.expires !== undefined && Date.now() >= entry.expires; }

function discoverCardReason(row, source) {
  if (source === "local") return discoverHtml("discover.sharedGenres", {genres: row.shared_genres.join(" · ")});
  const lastfm = source === "lastfm";
  const url = lastfm ? row.lastfm_url : "https://musicbrainz.org/artist/" + row.related_artist_mbid;
  const label = discoverHtml(lastfm ? "discover.relatedArtist" : "discover.relatedListenBrainz", {artist: row.related_artist});
  return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
}

function renderArtistDiscover() {
  if (artistDiscover.albums !== state.albums || artistDiscover.details !== state.details
    || artistDiscover.session !== state.connection.playback_session || artistDiscover.settings !== lookup.settingsRevision) resetArtistDiscover();
  const selection = discoverSelection(), section = $("#artist-discover");
  section.hidden = !selection;
  if (!selection) return;
  const key = discoverEntryKey(selection), entry = artistDiscover.entries.get(key), opened = entry?.open === true;
  const source = discoverSource(), effective = entry?.data?.source ?? source;
  $("#artist-discover-setup").hidden = source !== "lastfm" || lookup.sources.some(s => s.name === "lastfm" && s.enabled);
  const button = $("#artist-discover-toggle"), results = $("#artist-discover-results");
  $("#artist-discover-label").textContent = discoverText("discover.action");
  $("#artist-discover-source").textContent = discoverSourceLabel(effective);
  // Restoring a completed disclosure never starts a provider request.
  section.open = opened;
  button.setAttribute("aria-expanded", String(opened));
  results.hidden = !opened;
  $("#artist-discover-refresh").textContent = discoverText("discover.refresh");
  $("#artist-discover-refresh").disabled = Boolean(entry?.loading);
  const intro = {lastfm: "discover.introLastfm", listenbrainz: "discover.introListenBrainz"};
  $("#artist-discover-intro").textContent = discoverText(intro[entry?.data?.source] || "discover.intro", {artist: selection.name});
  const fallback = $("#artist-discover-fallback");
  fallback.hidden = !entry?.data?.fallback;
  fallback.textContent = discoverText(source === "listenbrainz" ? "discover.fallbackListenBrainz" : "discover.fallback");
  $("#artist-discover-attribution").hidden = entry?.data?.source !== "lastfm";
  $("#artist-discover-listenbrainz-attribution").hidden = entry?.data?.source !== "listenbrainz";
  const message = entry?.loading ? discoverText("discover.loading") : entry?.error ? (entry.error?.message || discoverText("discover.invalid_response"))
    : entry?.data?.status === "missing_genres" ? discoverText("discover.missingGenres")
    : entry?.data?.status === "no_matches" ? discoverText("discover.noMatches") : "";
  setActionStatus("#artist-discover-status", message, Boolean(entry?.loading));
  $("#artist-discover-status").classList.toggle("error-text", Boolean(entry?.error));
  if (!opened) return;
  const shelf = new Map(albumShelf().map(a => [String(a.id), a]));
  const cards = (entry?.data?.albums || []).map(row => {
    const album = shelf.get(String(row.id));
    if (!album) return "";
    return `<article class="discover-card"><button type="button" class="row-open discover-cover" data-album="${escapeHtml(album.id)}" aria-label="${discoverHtml("library.open.album", {name: album.name})}">${thumb(album)}</button><div class="discover-card-text"><button type="button" class="row-open row-title" data-album="${escapeHtml(album.id)}">${escapeHtml(album.name)}</button><div>${albumArtistLinks(album)}</div><p class="muted">${discoverCardReason(row, entry.data.source || "local")}</p></div>${row.playable ? albumCardActions(album) : `<small class="muted">${discoverHtml("discover.playbackUnavailable")}</small>`}</article>`;
  }).join("");
  const list = $("#artist-discover-cards");
  // Routine renders do not replace focused Play/Queue buttons or restart image loading.
  if (list.innerHTML !== cards) { if (typeof keepFocusWhile === "function") keepFocusWhile(list, () => { list.innerHTML = cards; }); else list.innerHTML = cards; }
}

// Leaving a page retires pending work, but retains completed, bounded results for Back.
function leaveArtistDiscover() {
  for (const [name, entry] of artistDiscover.entries) {
    if (entry.loading || discoverExpired(entry)) artistDiscover.entries.delete(name);
  }
  artistDiscover.request += 1;
}

async function loadArtistDiscover() {
  renderArtistDiscover();
  const selection = discoverSelection();
  if (!selection) return;
  const key = discoverEntryKey(selection), source = discoverSource(), settings = lookup.settingsRevision;
  if (artistDiscover.entries.get(key)?.loading) return;
  const entry = {open: true, loading: true, data: null, error: null};
  const albums = state.albums, details = state.details, request = ++artistDiscover.request;
  artistDiscover.entries.set(key, entry);
  while (artistDiscover.entries.size > 12) artistDiscover.entries.delete(artistDiscover.entries.keys().next().value);
  const current = () => request === artistDiscover.request && albums === state.albums && details === state.details
    && state.connection.playback_session === selection.session && state.screen === "albums"
    && state.artist === selection.name && live() && !state.loading
    && artistDiscover.entries.get(key) === entry && discoverSource() === source && lookup.settingsRevision === settings;
  renderArtistDiscover();
  try {
    const data = await api("/api/discover", {...selection, source});
    if (!current()) return;
    if (data.artist !== selection.name || data.session !== selection.session || !Array.isArray(data.albums)
      || (data.requested_source || "local") !== source
      || (data.source && data.source !== "local" && data.source !== source)
      || (source !== "local" && (typeof data.fallback !== "boolean" || !data.source
        || data.fallback !== (data.source === "local")))
      || (source !== "local" && (!Number.isInteger(data.expires_in) || data.expires_in < 0 || data.expires_in > 21600))
      || data.albums.length > 6 || !["ready", "missing_genres", "no_matches"].includes(data.status)
      || data.albums.some(row => !row || typeof row.id !== "string" || typeof row.playable !== "boolean"
        || !Array.isArray(row.shared_genres) || row.shared_genres.length > 3
        || row.shared_genres.some(g => typeof g !== "string" || g.length > 200)
        || (data.source && data.source !== "local" && (typeof row.related_artist !== "string" || !row.related_artist.trim() || row.related_artist.length > 200))
        || (data.source === "lastfm" && row.lastfm_url !== "https://www.last.fm/music/" + encodeURIComponent(row.related_artist).replace(/[!'()*]/g, c => "%" + c.charCodeAt(0).toString(16).toUpperCase()))
        || (data.source === "listenbrainz" && (typeof row.related_artist_mbid !== "string"
          || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(row.related_artist_mbid))))) {
      throw new Error(discoverText("discover.invalid_response"));
    }
    entry.data = data;
    if (source !== "local") entry.expires = Date.now() + data.expires_in * 1000;
  } catch (error) {
    if (current()) entry.error = error;
  } finally {
    if (current()) { entry.loading = false; renderArtistDiscover(); }
  }
}

$("#artist-discover-toggle").addEventListener("click", event => {
  // Native summary keyboard activation also dispatches click. Handle the state
  // here, not on toggle: programmatic restoration must remain read-only.
  event.preventDefault();
  const selection = discoverSelection();
  if (!selection) return;
  const key = discoverEntryKey(selection), entry = artistDiscover.entries.get(key);
  if (entry?.open) {
    entry.open = false;
    if (entry.loading || discoverExpired(entry)) { leaveArtistDiscover(); artistDiscover.entries.delete(key); }
    renderArtistDiscover();
  } else if (entry && !discoverExpired(entry)) { entry.open = true; renderArtistDiscover(); }
  else loadArtistDiscover();
});
$("#artist-discover-refresh").addEventListener("click", loadArtistDiscover);
