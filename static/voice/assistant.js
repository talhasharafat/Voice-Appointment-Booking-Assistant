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

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let sessionId = null;
  let busy = false;
  let recognition = null;
  let listening = false;

  function setStatus(message) {
    statusEl.textContent = message;
  }

  function appendMessage(role, text) {
    const item = document.createElement("li");
    item.className = role;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = role === "assistant" ? "Clinic desk" : "You";
    const body = document.createElement("div");
    body.textContent = text;
    item.appendChild(who);
    item.appendChild(body);
    transcriptEl.appendChild(item);
    item.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function speak(text) {
    if (!window.speechSynthesis || !text) {
      return;
    }
    window.speechSynthesis.cancel();
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
    window.speechSynthesis.speak(utterance);
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
            : "Listening for your next reply."
    );
    speak(data.message);

    // Only show a reference when the backend confirms a completed booking.
    if (data.booked && data.data && data.data.booking_reference) {
      bookingRefEl.hidden = false;
      bookingRefEl.textContent = "Booking reference: " + data.data.booking_reference;
    }

    if (data.status !== "active") {
      sessionId = data.booked ? data.session_id : null;
      micHint.textContent = data.booked ? "Booking finished" : "Start a new booking";
      startButton.disabled = false;
      endButton.disabled = true;
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
      speak(message);
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
    micHint.textContent = active ? "Listening… tap again to stop" : "Tap to speak";
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
      setStatus("Listening…");
    };
    recognition.onerror = function (event) {
      setListening(false);
      if (event.error === "not-allowed") {
        setStatus("Microphone permission blocked. Use the text box instead.");
      } else if (event.error !== "aborted") {
        setStatus("I could not hear clearly. Try again or type your reply.");
      }
    };
    recognition.onend = function () {
      setListening(false);
    };
    recognition.onresult = function (event) {
      const transcript = event.results[0] && event.results[0][0] && event.results[0][0].transcript;
      if (transcript) {
        sendTurn(transcript);
      } else {
        setStatus("I did not catch that. Please try again.");
      }
    };
    return recognition;
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
    const engine = ensureRecognition();
    if (!engine) {
      return;
    }
    if (listening) {
      engine.stop();
      return;
    }
    if (!sessionId) {
      startSession().then(function () {
        if (sessionId) {
          try {
            engine.start();
          } catch (error) {
            setStatus("Could not start the microphone.");
          }
        }
      });
      return;
    }
    window.speechSynthesis && window.speechSynthesis.cancel();
    try {
      engine.start();
    } catch (error) {
      setStatus("Could not start the microphone.");
    }
  }

  micButton.addEventListener("click", toggleMic);
  startButton.addEventListener("click", startSession);
  endButton.addEventListener("click", endSession);
  textForm.addEventListener("submit", function (event) {
    event.preventDefault();
    sendTurn(textInput.value);
  });

  if (!SpeechRecognition) {
    setStatus("Mic speech recognition is unavailable here. You can still book by typing.");
    micHint.textContent = "Typing mode";
  } else {
    setStatus("Free Browser Speech");
  }

  // Chrome loads voices asynchronously.
  if (window.speechSynthesis) {
    window.speechSynthesis.getVoices();
    window.speechSynthesis.onvoiceschanged = function () {
      window.speechSynthesis.getVoices();
    };
  }
})();
