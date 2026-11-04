(function () {
  const config = window.VOICE_CONFIG || {};
  const statusEl = document.getElementById("status");
  const micButton = document.getElementById("micButton");
  const micHint = document.getElementById("micHint");
  const startButton = document.getElementById("startButton");
  const endButton = document.getElementById("endButton");
  const transcriptEl = document.getElementById("transcript");
  const bookingRefEl = document.getElementById("bookingRef");
  const textForm = document.getElementById("textForm");
  const textInput = document.getElementById("textInput");
  const continuousMicInput = document.getElementById("continuousMic");

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let sessionId = null;
  let busy = false;
  let recognition = null;
  let listening = false;
  let wantListening = false;
  let speaking = false;
  let restartTimer = null;

  const STORAGE_KEY = "voiceContinuousMic";

  function continuousEnabled() {
    if (continuousMicInput) {
      return !!continuousMicInput.checked;
    }
    return !!config.continuousMic;
  }

  function loadContinuousPreference() {
    if (!continuousMicInput) {
      return;
    }
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "true" || saved === "false") {
      continuousMicInput.checked = saved === "true";
    } else {
      continuousMicInput.checked = !!config.continuousMic;
    }
  }

  function setStatus(message) {
    statusEl.textContent = message;
  }

  function renderRichText(container, text) {
    const lines = String(text || "").split("\n");
    const listItems = [];

    function flushList() {
      if (!listItems.length) {
        return;
      }
      const ol = document.createElement("ol");
      listItems.forEach(function (entry) {
        const li = document.createElement("li");
        li.textContent = entry;
        ol.appendChild(li);
      });
      container.appendChild(ol);
      listItems.length = 0;
    }

    lines.forEach(function (line) {
      const match = line.match(/^\s*(\d+)\.\s+(.*)$/);
      if (match) {
        listItems.push(match[2]);
        return;
      }
      flushList();
      if (!line.trim()) {
        container.appendChild(document.createElement("br"));
        return;
      }
      const row = document.createElement("div");
      row.textContent = line;
      container.appendChild(row);
    });
    flushList();
  }

  function appendMessage(role, text) {
    const item = document.createElement("li");
    item.className = role;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = role === "assistant" ? "Clinic desk" : "You";
    const body = document.createElement("div");
    body.className = "body";
    if (role === "assistant") {
      renderRichText(body, text);
    } else {
      body.textContent = text;
    }
    item.appendChild(who);
    item.appendChild(body);
    transcriptEl.prepend(item);
    item.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function speak(text) {
    if (!window.speechSynthesis || !text) {
      scheduleListenRestart(250);
      return Promise.resolve();
    }
    return new Promise(function (resolve) {
      window.speechSynthesis.cancel();
      speaking = true;
      pauseListeningForSpeech();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1;
      utterance.pitch = 1;
      const voices = window.speechSynthesis.getVoices();
      const preferred = voices.find(function (voice) {
        return /en-(GB|US|IN|PK)/i.test(voice.lang);
      });
      if (preferred) {
        utterance.voice = preferred;
      }
      utterance.onend = function () {
        speaking = false;
        resolve();
        scheduleListenRestart(350);
      };
      utterance.onerror = function () {
        speaking = false;
        resolve();
        scheduleListenRestart(350);
      };
      window.speechSynthesis.speak(utterance);
    });
  }

  async function postJson(url, payload) {
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
    } catch (error) {
      throw new Error(
        "I could not reach the clinic server and did not book an appointment. Check your connection and try again."
      );
    }

    let data;
    try {
      data = await response.json();
    } catch (error) {
      throw new Error(
        "The clinic server returned an unexpected response. No appointment was booked."
      );
    }

    if (!response.ok && data && data.error) {
      throw new Error(data.error.message || "Request failed. No appointment was booked.");
    }
    return data;
  }

  function applyAssistantPayload(data, options) {
    options = options || {};
    if (!data || !data.ok) {
      const message =
        (data && data.error && data.error.message) ||
        "Something went wrong and no appointment was booked.";
      setStatus(message);
      appendMessage("assistant", message);
      speak(message);
      return;
    }

    sessionId = data.session_id;
    endButton.disabled = data.status !== "active";
    micButton.disabled = data.status !== "active";
    startButton.disabled = data.status === "active";

    if (!options.skipTranscript) {
      appendMessage("assistant", data.message);
    }
    setStatus(
      data.booked
        ? "Booking confirmed."
        : data.status === "abandoned"
          ? "Session ended without a booking."
          : data.status === "failed"
            ? "Booking failed. Nothing was saved."
            : continuousEnabled()
              ? "Listening continuously…"
              : "Listening for your next reply."
    );
    speak(data.message);

    if (data.booked && data.data && data.data.booking_reference) {
      bookingRefEl.hidden = false;
      bookingRefEl.textContent = "Booking reference: " + data.data.booking_reference;
    }

    if (data.status !== "active") {
      wantListening = false;
      stopRecognition(true);
      sessionId = data.booked ? data.session_id : null;
      micHint.textContent = data.booked ? "Booking finished" : "Start a new booking";
      startButton.disabled = false;
      endButton.disabled = true;
    } else if (continuousEnabled()) {
      wantListening = true;
    }
  }

  async function startSession() {
    if (busy) {
      return;
    }
    busy = true;
    setStatus("Starting booking desk…");
    try {
      transcriptEl.innerHTML = "";
      bookingRefEl.hidden = true;
      bookingRefEl.textContent = "";
      window.speechSynthesis && window.speechSynthesis.cancel();
      const data = await postJson(config.startUrl, {});
      applyAssistantPayload(data);
      if (data && data.status === "active" && continuousEnabled()) {
        wantListening = true;
        startListening();
      }
    } catch (error) {
      setStatus(error.message || "Could not start session.");
    } finally {
      busy = false;
    }
  }

  async function sendTurn(text) {
    const cleaned = (text || "").trim();
    if (!cleaned || busy) {
      return;
    }
    if (!sessionId) {
      await startSession();
    }
    if (!sessionId) {
      return;
    }
    busy = true;
    pauseListeningForSpeech();
    appendMessage("user", cleaned);
    setStatus("Checking with the clinic calendar…");
    try {
      const data = await postJson(config.turnUrl, {
        session_id: sessionId,
        text: cleaned,
      });
      applyAssistantPayload(data);
    } catch (error) {
      const message = error.message || "I could not process that reply.";
      appendMessage("assistant", message);
      setStatus(message);
      await speak(message);
    } finally {
      busy = false;
      textInput.value = "";
    }
  }

  async function endSession() {
    if (!sessionId || busy) {
      return;
    }
    busy = true;
    wantListening = false;
    stopRecognition(true);
    try {
      const data = await postJson(config.abandonUrl, { session_id: sessionId });
      applyAssistantPayload(data);
      sessionId = null;
      startButton.disabled = false;
      endButton.disabled = true;
      micButton.disabled = false;
    } catch (error) {
      setStatus(error.message || "Could not end session.");
    } finally {
      busy = false;
    }
  }

  function setListening(active) {
    listening = active;
    micButton.classList.toggle("is-listening", active);
    micButton.setAttribute("aria-pressed", active ? "true" : "false");
    if (active) {
      micHint.textContent = continuousEnabled()
        ? "Listening continuously… tap mic to pause"
        : "Listening… tap again to stop";
    } else if (wantListening && continuousEnabled()) {
      micHint.textContent = "Microphone paused briefly…";
    } else {
      micHint.textContent = "Tap to speak";
    }
  }

  function stopRecognition(clearWant) {
    if (clearWant) {
      wantListening = false;
    }
    if (restartTimer) {
      window.clearTimeout(restartTimer);
      restartTimer = null;
    }
    if (recognition && listening) {
      try {
        recognition.stop();
      } catch (error) {
        // ignore
      }
    }
    setListening(false);
  }

  function pauseListeningForSpeech() {
    if (recognition && listening) {
      try {
        recognition.stop();
      } catch (error) {
        // ignore
      }
    }
  }

  function scheduleListenRestart(delayMs) {
    if (restartTimer) {
      window.clearTimeout(restartTimer);
    }
    restartTimer = window.setTimeout(function () {
      restartTimer = null;
      if (wantListening && continuousEnabled() && sessionId && !busy && !speaking) {
        startListening();
      }
    }, delayMs || 300);
  }

  function ensureRecognition() {
    if (!SpeechRecognition) {
      return null;
    }
    if (recognition) {
      return recognition;
    }
    recognition = new SpeechRecognition();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.continuous = false;

    recognition.onstart = function () {
      setListening(true);
      if (!busy && !speaking) {
        setStatus(continuousEnabled() ? "Listening continuously…" : "Listening…");
      }
    };
    recognition.onerror = function (event) {
      setListening(false);
      if (event.error === "not-allowed") {
        wantListening = false;
        setStatus("Microphone permission blocked. Use the text box instead.");
        return;
      }
      if (event.error === "aborted") {
        return;
      }
      if (event.error === "no-speech" && wantListening && continuousEnabled()) {
        scheduleListenRestart(400);
        return;
      }
      setStatus("I could not hear clearly. Try again or type your reply.");
      if (wantListening && continuousEnabled()) {
        scheduleListenRestart(800);
      }
    };
    recognition.onend = function () {
      setListening(false);
      if (wantListening && continuousEnabled() && sessionId && !busy && !speaking) {
        scheduleListenRestart(350);
      }
    };
    recognition.onresult = function (event) {
      const transcript = event.results[0] && event.results[0][0] && event.results[0][0].transcript;
      if (transcript) {
        sendTurn(transcript);
      } else if (wantListening && continuousEnabled()) {
        scheduleListenRestart(400);
      } else {
        setStatus("I did not catch that. Please try again.");
      }
    };
    return recognition;
  }

  function startListening() {
    if (!SpeechRecognition) {
      setStatus("This browser has no free Web Speech mic support. Type your reply below. Chrome works best.");
      textInput.focus();
      return;
    }
    if (busy || speaking) {
      scheduleListenRestart(400);
      return;
    }
    const engine = ensureRecognition();
    if (!engine || listening) {
      return;
    }
    window.speechSynthesis && window.speechSynthesis.cancel();
    try {
      engine.start();
    } catch (error) {
      scheduleListenRestart(600);
    }
  }

  function toggleMic() {
    if (busy) {
      return;
    }
    if (!SpeechRecognition) {
      setStatus("This browser has no free Web Speech mic support. Type your reply below. Chrome works best.");
      textInput.focus();
      return;
    }
    if (listening || wantListening) {
      wantListening = false;
      stopRecognition(true);
      setStatus("Microphone paused.");
      micHint.textContent = "Tap to speak";
      return;
    }
    if (!sessionId) {
      startSession();
      return;
    }
    wantListening = continuousEnabled();
    startListening();
  }

  micButton.addEventListener("click", toggleMic);
  startButton.addEventListener("click", startSession);
  endButton.addEventListener("click", endSession);
  textForm.addEventListener("submit", function (event) {
    event.preventDefault();
    sendTurn(textInput.value);
  });
  if (continuousMicInput) {
    loadContinuousPreference();
    continuousMicInput.addEventListener("change", function () {
      window.localStorage.setItem(STORAGE_KEY, continuousMicInput.checked ? "true" : "false");
      if (continuousMicInput.checked && sessionId && !busy) {
        wantListening = true;
        startListening();
      } else if (!continuousMicInput.checked) {
        wantListening = false;
        stopRecognition(true);
      }
    });
  }

  if (!SpeechRecognition) {
    setStatus("Mic speech recognition is unavailable here. You can still book by typing.");
    micHint.textContent = "Typing mode";
  } else {
    setStatus(
      continuousEnabled()
        ? "Ready · continuous mic can stay on after Start booking"
        : "Ready · tap mic for each reply"
    );
  }

  if (window.speechSynthesis) {
    window.speechSynthesis.getVoices();
    window.speechSynthesis.onvoiceschanged = function () {
      window.speechSynthesis.getVoices();
    };
  }
})();
