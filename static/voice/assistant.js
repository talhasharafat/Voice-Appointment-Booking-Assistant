(function () {
  const config = window.VOICE_CONFIG || {};
  const TOKEN_URL = config.tokenUrl || "/voice/api/livekit/token/";
  const LIVEKIT_URL = config.livekitUrl || "ws://127.0.0.1:7880";

  const LivekitClient = window.LivekitClient || window.LiveKit || window.livekit;
  const statusEl = document.getElementById("status");
  const micButton = document.getElementById("micButton");
  const micHint = document.getElementById("micHint");
  const startButton = document.getElementById("startButton");
  const endButton = document.getElementById("endButton");
  const transcriptEl = document.getElementById("transcript");
  const bookingRefEl = document.getElementById("bookingRef");
  const remoteAudio = document.getElementById("remoteAudio");

  let room = null;
  let connected = false;
  let connecting = false;
  const activeByRole = { user: null, agent: null };
  const flushTimers = { user: null, agent: null };
  const activeSegId = { user: null, agent: null };

  function setStatus(message) {
    statusEl.textContent = message;
  }

  function appendLine(role, text) {
    if (!text) return null;
    const li = document.createElement("li");
    li.className = role === "user" ? "user" : "agent";
    const label = document.createElement("strong");
    label.className = "who";
    label.textContent = role === "user" ? "You" : "Agent";
    const body = document.createElement("span");
    body.className = "body";
    body.textContent = text;
    li.appendChild(label);
    li.appendChild(body);
    transcriptEl.appendChild(li);
    li.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return { li: li, body: body };
  }

  function longestOverlap(a, b) {
    const max = Math.min(a.length, b.length);
    for (let k = max; k > 0; k--) {
      if (a.slice(-k) === b.slice(0, k)) return k;
    }
    return 0;
  }

  function mergeSpokenText(previous, next) {
    const prev = (previous || "").trim();
    const text = (next || "").trim();
    if (!text) return prev;
    if (!prev) return text;
    if (text === prev) return prev;
    if (text.startsWith(prev)) return text;
    if (prev.startsWith(text)) return prev;
    if (prev.endsWith(text)) return prev;

    // Growing rewrite of the trailing clause after '?'
    const q = prev.lastIndexOf("?");
    if (q >= 0) {
      const head = prev.slice(0, q + 1).trim();
      const tail = prev.slice(q + 1).trim();
      if (!tail) return (head + " " + text).replace(/\s+/g, " ").trim();
      if (text.startsWith(tail) || tail.startsWith(text)) {
        const better = text.length >= tail.length ? text : tail;
        return (head + " " + better).replace(/\s+/g, " ").trim();
      }
      // Progressive stutter fragments: replace tail with the longer next phrase.
      if (text.length > 8 && (tail.indexOf(text.slice(0, 8)) >= 0 || text.indexOf(tail.slice(0, Math.min(8, tail.length))) >= 0)) {
        return (head + " " + (text.length >= tail.length ? text : tail)).replace(/\s+/g, " ").trim();
      }
    }

    const overlap = longestOverlap(prev, text);
    if (overlap >= 3) {
      return (prev + text.slice(overlap)).replace(/\s+/g, " ").trim();
    }

    // Prefer the longer string when one roughly contains the other.
    if (prev.length > 20 && text.indexOf(prev.slice(0, 20)) >= 0) return text;
    if (text.length > 20 && prev.indexOf(text.slice(0, 20)) >= 0) return prev;

    return (prev + " " + text).replace(/\s+/g, " ").trim();
  }

  function closeRole(role) {
    if (flushTimers[role]) {
      clearTimeout(flushTimers[role]);
      flushTimers[role] = null;
    }
    activeByRole[role] = null;
    activeSegId[role] = null;
  }

  function upsertSpoken(role, text, isFinal, segId) {
    if (!text) return;
    let row = activeByRole[role];

    if (segId && activeSegId[role] && segId === activeSegId[role] && row) {
      row.body.textContent = mergeSpokenText(row.body.textContent, text);
      row.li.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } else if (!row && role === "agent") {
      const last = transcriptEl.lastElementChild;
      if (last && last.classList.contains("agent") && !last.classList.contains("card")) {
        const body = last.querySelector(".body");
        const prev = (body && body.textContent) || "";
        if (
          text === prev ||
          text.startsWith(prev) ||
          prev.startsWith(text) ||
          longestOverlap(prev, text) >= 8
        ) {
          row = { li: last, body: body };
          activeByRole[role] = row;
          row.body.textContent = mergeSpokenText(prev, text);
        }
      }
    }

    if (!row) {
      row = appendLine(role, text);
      activeByRole[role] = row;
      if (segId) activeSegId[role] = segId;
    } else if (!(segId && activeSegId[role] === segId)) {
      row.body.textContent = mergeSpokenText(row.body.textContent, text);
      row.li.scrollIntoView({ behavior: "smooth", block: "nearest" });
      if (segId) activeSegId[role] = segId;
    }

    if (flushTimers[role]) clearTimeout(flushTimers[role]);
    flushTimers[role] = setTimeout(
      function () {
        closeRole(role);
      },
      isFinal ? (role === "agent" ? 1800 : 150) : role === "agent" ? 2200 : 1000
    );
  }

  function formatSlot(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      return d.toLocaleString(undefined, {
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
    } catch (err) {
      return String(iso);
    }
  }

  function appendCard(title, rows, opts) {
    opts = opts || {};
    const li = document.createElement("li");
    li.className = "agent card";
    if (opts.tone) li.classList.add("card--" + opts.tone);

    const who = document.createElement("strong");
    who.className = "who";
    who.textContent = opts.who || "Details";
    li.appendChild(who);

    const heading = document.createElement("div");
    heading.className = "card__title";
    heading.textContent = title;
    li.appendChild(heading);

    if (rows && rows.length) {
      const list = document.createElement("ol");
      list.className = "card__list";
      rows.forEach(function (row) {
        const item = document.createElement("li");
        if (typeof row === "string") {
          item.textContent = row;
        } else {
          const name = document.createElement("div");
          name.className = "card__name";
          name.textContent = row.title || "";
          item.appendChild(name);
          if (row.meta) {
            const meta = document.createElement("div");
            meta.className = "card__meta";
            meta.textContent = row.meta;
            item.appendChild(meta);
          }
        }
        list.appendChild(item);
      });
      li.appendChild(list);
    }

    if (opts.footnote) {
      const foot = document.createElement("p");
      foot.className = "card__foot";
      foot.textContent = opts.footnote;
      li.appendChild(foot);
    }

    transcriptEl.appendChild(li);
    li.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return li;
  }

  function handleUiEvent(parsed) {
    const type = parsed && parsed.type;
    if (!type) return;

    if (type === "doctors") {
      const doctors = parsed.doctors || [];
      const rows = doctors.map(function (d, idx) {
        const slots = (d.next_open_slots || [])
          .slice(0, 3)
          .map(function (s) {
            return formatSlot(s.starts_at || s);
          })
          .filter(Boolean);
        return {
          title: idx + 1 + ". " + (d.name || "Doctor"),
          meta: slots.length ? "Next: " + slots.join(" · ") : "Ask for available times",
        };
      });
      appendCard(
        parsed.specialty_name
          ? "Doctors for " + parsed.specialty_name
          : "Available doctors",
        rows,
        { who: "Doctors", footnote: "Say the doctor name or number." }
      );
      return;
    }

    if (type === "slots") {
      const slots = parsed.slots || [];
      const rows = slots.map(function (s, idx) {
        return idx + 1 + ". " + formatSlot(s.starts_at || s);
      });
      const title = parsed.doctor_name
        ? "Open times — " + parsed.doctor_name
        : "Open times";
      appendCard(title, rows, {
        who: "Schedule",
        footnote:
          parsed.preferred_available === false
            ? "Preferred time unavailable — pick one of these."
            : "Say a time from the list.",
      });
      return;
    }

    if (type === "confirmation") {
      const rows = [
        { title: "Patient", meta: parsed.patient_name || "—" },
        { title: "Phone", meta: parsed.phone || "—" },
        { title: "Doctor", meta: parsed.doctor_name || "—" },
        { title: "Time", meta: formatSlot(parsed.slot_start) || "—" },
        { title: "Reason", meta: parsed.reason || "—" },
      ];
      appendCard("Please confirm this booking", rows, {
        who: "Confirmation",
        tone: "confirm",
        footnote: "Say yes to book, or no to cancel.",
      });
      return;
    }

    if (type === "booking") {
      const ref = parsed.booking_reference || "";
      const rows = [
        { title: "Reference", meta: ref || "—" },
        { title: "Patient", meta: parsed.patient_name || "—" },
        { title: "Doctor", meta: parsed.doctor_name || "—" },
        { title: "Time", meta: formatSlot(parsed.slot_start) || "—" },
        { title: "Reason", meta: parsed.reason || "—" },
      ];
      appendCard("Appointment confirmed", rows, {
        who: "Booking",
        tone: "success",
      });
      if (bookingRefEl && ref) {
        bookingRefEl.hidden = false;
        bookingRefEl.textContent = "Booking reference: " + ref;
      }
      setStatus("Booked — reference " + ref);
      return;
    }

    if (type === "specialty" && parsed.clarifying_question) {
      appendCard("Need a quick clarification", [], {
        who: "Specialty",
        footnote: parsed.clarifying_question,
      });
    }
  }

  function setConnected(isOn) {
    connected = isOn;
    micButton.setAttribute("aria-pressed", isOn ? "true" : "false");
    startButton.disabled = isOn;
    endButton.disabled = !isOn;
    micHint.textContent = isOn ? "Connected — speak naturally" : "Tap to connect and speak";
  }

  async function fetchToken() {
    const response = await fetch(TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      throw new Error("Token endpoint returned non-JSON (HTTP " + response.status + ").");
    }
    if (!response.ok || !data.ok) {
      throw new Error(
        (data.error && data.error.message) ||
          "Could not get LiveKit token (HTTP " + response.status + ")."
      );
    }
    if (!data.token) {
      throw new Error("Token endpoint did not return a token.");
    }
    return data;
  }

  function attachRemoteAudio(track) {
    const el = track.attach();
    el.autoplay = true;
    el.playsInline = true;
    remoteAudio.appendChild(el);
    const playAttempt = el.play();
    if (playAttempt && playAttempt.catch) {
      playAttempt.catch(function () {
        setStatus("Tap the page if you cannot hear the agent (browser autoplay).");
      });
    }
  }

  async function connectRoom() {
    if (connecting) return;
    if (!LivekitClient || !LivekitClient.Room) {
      setStatus(
        "LiveKit browser client failed to load. Check the CDN script, then hard-refresh the page."
      );
      return;
    }
    if (connected) {
      await disconnectRoom();
      return;
    }

    connecting = true;
    setStatus("Connecting to LiveKit…");
    try {
      const creds = await fetchToken();
      const serverUrl = creds.url || LIVEKIT_URL;
      if (!serverUrl || serverUrl === "undefined") {
        throw new Error(
          "LiveKit URL is missing. Set LIVEKIT_URL=ws://127.0.0.1:7880 in .env and restart Django."
        );
      }

      room = new LivekitClient.Room({ adaptiveStream: true, dynacast: true });

      room.on(LivekitClient.RoomEvent.TrackSubscribed, function (track, publication, participant) {
        if (
          track.kind === "audio" ||
          (LivekitClient.Track && track.kind === LivekitClient.Track.Kind.Audio)
        ) {
          attachRemoteAudio(track);
          var who = (participant && participant.identity) || "agent";
          setStatus("Connected to " + who + ". Wait for the greeting, then answer.");
        }
      });

      room.on(LivekitClient.RoomEvent.ParticipantConnected, function () {
        setStatus("Agent joined the room. Preparing greeting…");
      });

      room.on(LivekitClient.RoomEvent.TrackUnsubscribed, function (track) {
        track.detach().forEach(function (el) {
          el.remove();
        });
      });

      room.on(LivekitClient.RoomEvent.Disconnected, function () {
        setConnected(false);
        setStatus("Disconnected. No appointment is saved until you confirm a booking.");
      });

      room.on(LivekitClient.RoomEvent.DataReceived, function (payload) {
        try {
          const text = new TextDecoder().decode(payload);
          const parsed = JSON.parse(text);
          if (!parsed) return;
          if (parsed.type) {
            handleUiEvent(parsed);
            return;
          }
          if (parsed.text && (parsed.role === "system" || parsed.type === "booking")) {
            appendLine(parsed.role || "agent", parsed.text);
          }
        } catch (err) {
          // Ignore non-JSON data packets.
        }
      });

      if (LivekitClient.RoomEvent.TranscriptionReceived) {
        room.on(LivekitClient.RoomEvent.TranscriptionReceived, function (segments, participant) {
          const isLocal =
            participant &&
            room.localParticipant &&
            participant.identity === room.localParticipant.identity;
          const role = isLocal ? "user" : "agent";
          (segments || []).forEach(function (seg) {
            const text = (seg.text || "").trim();
            if (!text) return;
            const isFinal = seg.final === true || seg.isFinal === true;
            const segId = seg.id || seg.segmentId || seg.sid || null;
            upsertSpoken(role, text, isFinal, segId);
          });
        });
      }

      await room.connect(serverUrl, creds.token);
      await room.localParticipant.setMicrophoneEnabled(true);
      setConnected(true);
      setStatus("Connected. Waiting for the local agent worker…");
    } finally {
      connecting = false;
    }
  }

  async function disconnectRoom() {
    if (room) {
      await room.disconnect();
      room = null;
    }
    closeRole("user");
    closeRole("agent");
    remoteAudio.innerHTML = "";
    setConnected(false);
    setStatus("Ended. Nothing was booked unless the agent confirmed a reference.");
  }

  function onConnectClick() {
    connectRoom().catch(function (err) {
      console.error(err);
      setStatus(err.message || "Could not connect.");
      setConnected(false);
    });
  }

  micButton.addEventListener("click", onConnectClick);
  startButton.addEventListener("click", onConnectClick);
  endButton.addEventListener("click", function () {
    disconnectRoom().catch(function () {
      setConnected(false);
    });
  });
})();
