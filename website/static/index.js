const chat = document.getElementById("chat");
const input = document.getElementById("input");
const send = document.getElementById("send");
const requestHint = document.getElementById("request-hint");
const sidebar = document.querySelector(".sidebar");
const prReviewPanel = document.getElementById("pr-review-panel");
const mainChat = document.getElementById("main-chat");
const prReviewBtn = document.getElementById("pr-review-btn");
const prReviewClose = document.getElementById("pr-review-close");
const backToChatBtn = document.getElementById("back-to-chat-btn");
const startReviewBtn = document.getElementById("start-review-btn");
const parsePrBtn = document.getElementById("parse-pr-btn");
const prLinkInput = document.getElementById("pr-link-input");
const prInfo = document.getElementById("pr-info");
const prDisplay = document.getElementById("pr-display");
const checklistItems = document.getElementById("checklist-items");
const selectAllBtn = document.getElementById("select-all-btn");
const deselectAllBtn = document.getElementById("deselect-all-btn");
const prPanelModal = prReviewPanel ? prReviewPanel.querySelector(".panel-modal") : null;

if (prReviewPanel) {
    prReviewPanel.addEventListener("click", function(event) {
        if (event.target === prReviewPanel) {
            prReviewPanel.style.display = "none";
            mainChat.style.display = "flex";
            sidebar.style.display = "";
        }
    });
}
if (prPanelModal) {
    prPanelModal.addEventListener("click", function(event) {
        event.stopPropagation();
    });
}
const welcomeTyping = document.getElementById("welcome-typing");
const welcomeCursor = document.getElementById("welcome-cursor");

function typeWelcomeMessage() {
    if (!welcomeTyping) return;
    const message = "Hi! I am MunnAI, how may I help you today?";
    let index = 0;
    welcomeTyping.textContent = "";

    function step() {
        if (index < message.length) {
            welcomeTyping.textContent += message.charAt(index);
            index += 1;
            setTimeout(step, 40);
            return;
        }
        if (welcomeCursor) {
            welcomeCursor.style.display = "none";
        }
    }

    step();
}

typeWelcomeMessage();

function createStreamingBubble() {
    const bubble = document.createElement("div");
    bubble.className = "msg bot";

    bubble.innerHTML = `
        <div class="msg-inner">
            <div class="msg-body">
                <span class="streaming-text"></span>
                <span class="cursor">▋</span>
            </div>
        </div>
    `;

    chat.appendChild(bubble);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;

    return {
        textEl: bubble.querySelector(".streaming-text"),
        cursorEl: bubble.querySelector(".cursor")
    };
}
function streamText(textEl, cursorEl, text, speed = 12) {
    let i = 0;

    function step() {
        if (i < text.length) {
            textEl.innerHTML += text[i];
            i++;

            chat.parentElement.scrollTop = chat.parentElement.scrollHeight;

            requestAnimationFrame(() => {
                setTimeout(step, speed);
            });
        } else {
            // ✅ STOP blinking when done
            if (cursorEl) cursorEl.remove();
        }
    }

    step();
}

function createTypingIndicator() {
    const bubble = document.createElement("div");
    bubble.className = "msg bot typing";

    bubble.innerHTML = `
        <div class="msg-inner">
            <div class="msg-body">
                <div class="typing-indicator">
                    <span></span><span></span><span></span>
                </div>
            </div>
        </div>
    `;

    chat.appendChild(bubble);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;

    return bubble;
}

// --- TYPING (3 dots) ---
function createTypingBubble() {
    const bubble = document.createElement("div");
    bubble.className = "msg bot typing-bubble";

    bubble.innerHTML = `
        <div class="msg-inner">
            <div class="msg-body">
                <span class="dot"></span>
                <span class="dot"></span>
                <span class="dot"></span>
            </div>
        </div>
    `;

    chat.appendChild(bubble);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;

    return bubble;
}

function removeTypingBubble(bubble) {
    if (bubble && bubble.parentNode) {
        bubble.remove();
    }
}


// --- STREAMING TEXT (ChatGPT style) ---
function createStreamingBubble() {
    const bubble = document.createElement("div");
    bubble.className = "msg bot";

    bubble.innerHTML = `
        <div class="msg-inner">
            <div class="msg-body">
                <span class="stream-text"></span><span class="cursor">|</span>
            </div>
        </div>
    `;

    chat.appendChild(bubble);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;

    return {
        textEl: bubble.querySelector(".stream-text"),
        cursorEl: bubble.querySelector(".cursor")
    };
}

function streamText(textEl, cursorEl, text, speed = 12) {
    let i = 0;

    function step() {
        if (i < text.length) {
            textEl.innerHTML += text.charAt(i)
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/\n/g, "<br>");

            i++;
            chat.parentElement.scrollTop = chat.parentElement.scrollHeight;

            setTimeout(step, speed);
        } else {
            // ✅ STOP blinking when done
            if (cursorEl) cursorEl.remove();
        }
    }

    step();
}

// --- PR Review Panel ---
prReviewBtn.addEventListener("click", function() {
    prReviewPanel.style.display = "flex";
    mainChat.style.display = "none";
    sidebar.style.display = "none";
});
prReviewClose.addEventListener("click", function() {
    prReviewPanel.style.display = "none";
    mainChat.style.display = "flex";
    sidebar.style.display = "";
});
backToChatBtn.addEventListener("click", function() {
    prReviewPanel.style.display = "none";
    mainChat.style.display = "flex";
    sidebar.style.display = "";
});


// Parse PR link
parsePrBtn.addEventListener("click", function() {
    var link = prLinkInput.value.trim();
    var match = link.match(/github\.com\/([^\/]+)\/([^\/]+)\/pull\/(\d+)/);
    if (match) {
        prDisplay.textContent = match[1] + "/" + match[2] + "#" + match[3];
        prInfo.style.display = "block";
    } else {
        alert("Invalid PR link. Use: https://github.com/owner/repo/pull/123");
    }
});

// Select All / Deselect All for checklist
selectAllBtn.addEventListener("click", function() {
    checklistItems.querySelectorAll("input[type=checkbox]").forEach(function(cb) { cb.checked = true; });
});
deselectAllBtn.addEventListener("click", function() {
    checklistItems.querySelectorAll("input[type=checkbox]").forEach(function(cb) { cb.checked = false; });
});


// Add expected output item
document.getElementById("add-output-btn").addEventListener("click", function() {
    var input = document.getElementById("add-output-input");
    var text = input.value.trim();
    if (!text) return;
    var div = document.createElement("div");
    div.className = "check-item output-item";
    div.innerHTML = '<input type="checkbox" checked><span>' + text.replace(/</g, "&lt;").replace(/>/g, "&gt;") + '</span>';
    document.getElementById("output-options").appendChild(div);
    div.querySelector(".remove-output-btn").addEventListener("click", function() { div.remove(); });
    input.value = "";
});
document.getElementById("add-output-input").addEventListener("keydown", function(e) {
    if (e.key === "Enter") { e.preventDefault(); document.getElementById("add-output-btn").click(); }
});



// Start Review - builds prompt and sends to chat
startReviewBtn.addEventListener("click", function() {
    var prLink = prLinkInput.value.trim();
    if (!prLink) { alert("Please enter a PR link"); return; }
    var match = prLink.match(/github\.com\/([^\/]+)\/([^\/]+)\/pull\/(\d+)/);
    if (!match) { alert("Invalid PR link format"); return; }

    // Gather selected expected outputs (dynamic)
    var outputs = [];
    document.querySelectorAll("#output-options .output-item").forEach(function(item) {
        if (item.querySelector("input").checked) {
            outputs.push(item.querySelector("span").textContent.trim());
        }
    });

    if (outputs.length === 0) { alert("Please select at least one expected output type"); return; }

    // Gather selected checklist items
    var items = checklistItems.querySelectorAll(".check-item");
    var checklist = [];
    items.forEach(function(item) {
        if (item.querySelector("input").checked) {
            checklist.push(item.querySelector("span").textContent);
        }
    });

    var owner = match[1], repo = match[2], prNum = match[3];

    // Build the prompt
    var msg = "Review this GitHub PR: https://github.com/" + owner + "/" + repo + "/pull/" + prNum;

    // Add expected output instructions
    msg += "\n\nExpected output:\n- " + outputs.join("\n- ");

    // Add checklist
    if (checklist.length > 0) {
        msg += "\n\nChecklist items to review:\n- " + checklist.join("\n- ");
    }

    // Switch to chat and send review request
    prReviewPanel.style.display = "none";
    mainChat.style.display = "flex";
    sidebar.style.display = "";
    prLinkInput.value = "";
    prInfo.style.display = "none";

    // Show the full review request in chat bubble
    var userMsg = document.createElement("div");
    userMsg.className = "msg user";
    userMsg.innerHTML = '<div class="msg-inner"><div class="msg-body"></div></div>';
    userMsg.querySelector(".msg-body").textContent = msg;
    chat.appendChild(userMsg);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;

    chatHistory.push({role: "user", text: msg});
    requestHint.textContent = "Sending request...";
    requestHint.style.color = "#666";

    // Stream live progress via SSE
    var botBubble = document.createElement("div");
    botBubble.className = "msg bot";
    botBubble.innerHTML = '<div class="msg-inner"><div class="msg-body"><span class="sse-progress"></span></div></div>';
    chat.appendChild(botBubble);
    var progressEl = botBubble.querySelector(".sse-progress");
    var progressLines = [];

    function appendProgress(text, cssClass) {
        var line = document.createElement("div");
        var now = new Date();
        var ts = now.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
        line.innerHTML = '<span class="sse-time">' + ts + '</span> ' + text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
        if (cssClass) line.className = cssClass;
        progressEl.appendChild(line);
        chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
    }

    appendProgress("\u23f3 Starting review...", "sse-line sse-info");

    (async function() {
        try {
            var res = await fetch("/api/review-stream", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({prompt: msg, history: chatHistory, checklist: checklist, outputs: outputs})
            });

            if (!res.ok || !res.body) {
                // Fallback: try regular chat endpoint
                appendProgress("\u2022 Streaming unavailable, using direct review...", "sse-line sse-info");
                var res2 = await fetch("/api/chat", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({prompt: msg, history: chatHistory})
                });
                var data2 = await res2.json();
                var resp = data2.response || data2.error || "No response";
                appendProgress("\u2705 " + resp.split("\n")[0], "sse-line sse-done");
                chatHistory.push({role: "assistant", text: resp});
                requestHint.textContent = "Review complete";
                requestHint.style.color = "#2e7d32";
                return;
            }

            var reader = res.body.getReader();
            var decoder = new TextDecoder();
            var buffer = "";
            var finalMessage = "";

            while (true) {
                var chunk = await reader.read();
                if (chunk.done) break;
                buffer += decoder.decode(chunk.value, {stream: true});

                var parts = buffer.split("\n\n");
                buffer = parts.pop();

                for (var i = 0; i < parts.length; i++) {
                    var part = parts[i].trim();
                    if (!part.startsWith("data: ")) continue;
                    try {
                        var evt = JSON.parse(part.substring(6));
                        if (evt.type === "progress") {
                            appendProgress("\u2022 " + evt.message, "sse-line sse-step");
                            requestHint.textContent = evt.message;
                        } else if (evt.type === "done") {
                            finalMessage = evt.message;
                            appendProgress("\u2705 " + evt.message.split("\n")[0], "sse-line sse-done");
                            requestHint.textContent = "Review complete";
                            requestHint.style.color = "#2e7d32";
                        } else if (evt.type === "error") {
                            finalMessage = evt.message;
                            appendProgress("\u274c " + evt.message, "sse-line sse-error");
                            requestHint.textContent = "Review failed";
                            requestHint.style.color = "#d32f2f";
                        }
                    } catch(e) {}
                }
            }

            chatHistory.push({role: "assistant", text: finalMessage || "Review completed"});
        } catch (err) {
            // Fallback: try regular chat endpoint
            appendProgress("\u2022 Retrying with direct review...", "sse-line sse-info");
            try {
                var res3 = await fetch("/api/chat", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({prompt: msg, history: chatHistory})
                });
                var data3 = await res3.json();
                var resp3 = data3.response || data3.error || "No response";
                appendProgress("\u2705 " + resp3.split("\n")[0], "sse-line sse-done");
                chatHistory.push({role: "assistant", text: resp3});
                requestHint.textContent = "Review complete";
                requestHint.style.color = "#2e7d32";
            } catch (err2) {
                appendProgress("\u274c Error: " + err2.message, "sse-line sse-error");
                requestHint.textContent = "Error: " + err2.message;
                requestHint.style.color = "#d32f2f";
            }
        }
    })();
});

// --- Original Chat ---
var chatHistory = [];

send.addEventListener("click", async function() {
    var msg = input.value.trim();
    if (!msg) return;

    input.value = "";

    // --- USER MESSAGE ---
    var userMsg = document.createElement("div");
    userMsg.className = "msg user";
    userMsg.innerHTML = '<div class="msg-inner"><div class="msg-body"></div></div>';
    userMsg.querySelector(".msg-body").textContent = msg;
    chat.appendChild(userMsg);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;

    chatHistory.push({role: "user", text: msg});

    requestHint.textContent = "Processing...";
    requestHint.style.color = "#666";

    // ✅ SHOW TYPING DOTS IMMEDIATELY
    const typingBubble = createTypingBubble();

    try {
        var res = await fetch("/api/chat", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({prompt: msg, history: chatHistory})
        });

        var data = await res.json();
        var response = data.response || data.error || "No response";

        // ✅ REMOVE TYPING DOTS
        removeTypingBubble(typingBubble);

        // ✅ STREAM RESPONSE
        const { textEl, cursorEl } = createStreamingBubble();
        streamText(textEl, cursorEl, response, 10);

        chatHistory.push({role: "assistant", text: response});

        requestHint.textContent = "Ready for next request";
        requestHint.style.color = "#999";

    } catch (err) {
        // ❌ ERROR HANDLING
        removeTypingBubble(typingBubble);

        var errorMsg = document.createElement("div");
        errorMsg.className = "msg bot";
        errorMsg.innerHTML = `
            <div class="msg-inner" style="border-color:#d32f2f;color:#d32f2f;">
                <div class="msg-body">Error: ${(err.message || "Unknown error").replace(/</g,"&lt;")}</div>
            </div>
        `;
        chat.appendChild(errorMsg);

        requestHint.textContent = "Error occurred";
        requestHint.style.color = "#d32f2f";
    }
});

input.addEventListener("keydown", function(e) {
    if (e.ctrlKey && e.key === "Enter") { send.click(); }
});

// --- Confluence Review Panel ---
var confPanel = document.getElementById("confluence-review-panel");
var confReviewBtn = document.getElementById("confluence-review-btn");
var confCloseBtn = document.getElementById("confluence-review-close");
var confBackBtn = document.getElementById("confluence-back-to-chat-btn");
var confPanelModal = confPanel ? confPanel.querySelector(".panel-modal") : null;

if (confPanel) {
    confPanel.addEventListener("click", function(event) {
        if (event.target === confPanel) {
            confPanel.style.display = "none";
            mainChat.style.display = "flex";
            sidebar.style.display = "";
        }
    });
}
if (confPanelModal) {
    confPanelModal.addEventListener("click", function(event) {
        event.stopPropagation();
    });
}
var confStartBtn = document.getElementById("start-confluence-review-btn");
var confLinkInput = document.getElementById("confluence-link-input");
var confParseBtn = document.getElementById("parse-confluence-btn");
var confInfo = document.getElementById("confluence-info");
var confDisplay = document.getElementById("confluence-display");
var confDocType = document.getElementById("confluence-doc-type");
var confChecklistItems = document.getElementById("confluence-checklist-items");

confReviewBtn.addEventListener("click", function() {
    confPanel.style.display = "flex";
    mainChat.style.display = "none";
    sidebar.style.display = "none";
});
confCloseBtn.addEventListener("click", function() {
    confPanel.style.display = "none";
    mainChat.style.display = "flex";
    sidebar.style.display = "";
});
confBackBtn.addEventListener("click", function() {
    confPanel.style.display = "none";
    mainChat.style.display = "flex";
    sidebar.style.display = "";
});

// Parse Confluence link
confParseBtn.addEventListener("click", function() {
    var link = confLinkInput.value.trim();
    // Try URL pattern: .../pages/12345/... or .../pages/12345
    var match = link.match(/pages\/([0-9]+)/);
    if (match) {
        confDisplay.textContent = "Page ID: " + match[1];
        confInfo.style.display = "block";
        return;
    }
    // Try raw numeric page ID
    if (/^\d+$/.test(link)) {
        confDisplay.textContent = "Page ID: " + link;
        confInfo.style.display = "block";
        return;
    }
    alert("Could not parse Confluence page. Use a URL with /pages/12345 or enter a numeric page ID.");
});

// Confluence checklist controls
document.getElementById("confluence-select-all-btn").addEventListener("click", function() {
    confChecklistItems.querySelectorAll("input[type=checkbox]").forEach(function(cb) { cb.checked = true; });
});
document.getElementById("confluence-deselect-all-btn").addEventListener("click", function() {
    confChecklistItems.querySelectorAll("input[type=checkbox]").forEach(function(cb) { cb.checked = false; });
});
// Add confluence expected output item
document.getElementById("confluence-add-output-btn").addEventListener("click", function() {
    var inp = document.getElementById("confluence-add-output-input");
    var text = inp.value.trim();
    if (!text) return;
    var div = document.createElement("div");
    div.className = "check-item output-item";
    div.innerHTML = '<input type="checkbox" checked><span>' + text.replace(/</g, "&lt;").replace(/>/g, "&gt;") + '</span>';
    document.getElementById("confluence-output-options").appendChild(div);
    inp.value = "";
});
document.getElementById("confluence-add-output-input").addEventListener("keydown", function(e) {
    if (e.key === "Enter") { e.preventDefault(); document.getElementById("confluence-add-output-btn").click(); }
});

// Start Confluence Review
confStartBtn.addEventListener("click", function() {
    var link = confLinkInput.value.trim();
    if (!link) { alert("Please enter a Confluence page URL or ID"); return; }

    // Extract page ID
    var pageId = null;
    var match = link.match(/pages\/([0-9]+)/);
    if (match) {
        pageId = match[1];
    } else if (/^\d+$/.test(link)) {
        pageId = link;
    } else {
        alert("Could not parse page ID from input.");
        return;
    }

    // Gather document type
    var docType = confDocType.value;

    // Gather selected checklist items
    var checklist = [];
    confChecklistItems.querySelectorAll(".check-item").forEach(function(item) {
        if (item.querySelector("input").checked) {
            checklist.push(item.querySelector("span").textContent);
        }
    });

    // Gather selected expected outputs
    var outputs = [];
    document.querySelectorAll("#confluence-output-options .output-item").forEach(function(item) {
        if (item.querySelector("input").checked) {
            outputs.push(item.querySelector("span").textContent.trim());
        }
    });
    if (outputs.length === 0) { alert("Please select at least one expected output type"); return; }

    // Build prompt
    var msg = "Review this Confluence page: " + link;
    if (docType) {
        msg += "\n\nDocument type: " + docType;
    }
    msg += "\n\nExpected output:\n- " + outputs.join("\n- ");
    if (checklist.length > 0) {
        msg += "\n\nChecklist items to review:\n- " + checklist.join("\n- ");
    }

    // Switch to chat
    confPanel.style.display = "none";
    mainChat.style.display = "flex";
    sidebar.style.display = "";

    // Show user message in chat
    var userMsg = document.createElement("div");
    userMsg.className = "msg user";
    userMsg.innerHTML = '<div class="msg-inner"><div class="msg-body"></div></div>';
    userMsg.querySelector(".msg-body").textContent = msg;
    chat.appendChild(userMsg);
    setTimeout(() => {
        chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
    }, 0);

    chatHistory.push({role: "user", text: msg});
    requestHint.textContent = "Sending request...";
    requestHint.style.color = "#666";

    // SSE live progress
    var botBubble = document.createElement("div");
    botBubble.className = "msg bot";
    botBubble.innerHTML = '<div class="msg-inner"><div class="msg-body"><span class="sse-progress"></span></div></div>';
    chat.appendChild(botBubble);
    var progressEl = botBubble.querySelector(".sse-progress");

    function confAppendProgress(text, cssClass) {
        var line = document.createElement("div");
        var now = new Date();
        var ts = now.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
        line.innerHTML = '<span class="sse-time">' + ts + '</span> ' + text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
        if (cssClass) line.className = cssClass;
        progressEl.appendChild(line);
        chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
    }

    confAppendProgress("\u23f3 Starting Confluence review...", "sse-line sse-info");

    (async function() {
        try {
            var res = await fetch("/api/confluence-review-stream", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    page_id: pageId,
                    page_input: link,
                    doc_type: docType,
                    checklist: checklist,
                    outputs: outputs,
                    prompt: msg,
                    history: chatHistory
                })
            });

            if (!res.ok || !res.body) {
                confAppendProgress("\u2022 Streaming unavailable, using direct review...", "sse-line sse-info");
                var res2 = await fetch("/api/chat", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({prompt: msg, history: chatHistory})
                });
                var data2 = await res2.json();
                var resp = data2.response || data2.error || "No response";
                confAppendProgress("\u2705 " + resp.split("\n")[0], "sse-line sse-done");
                chatHistory.push({role: "assistant", text: resp});
                requestHint.textContent = "Review complete";
                requestHint.style.color = "#2e7d32";
                return;
            }

            var reader = res.body.getReader();
            var decoder = new TextDecoder();
            var buffer = "";
            var finalMessage = "";

            while (true) {
                var chunk = await reader.read();
                if (chunk.done) break;
                buffer += decoder.decode(chunk.value, {stream: true});

                var parts = buffer.split("\n\n");
                buffer = parts.pop();

                for (var i = 0; i < parts.length; i++) {
                    var part = parts[i].trim();
                    if (!part.startsWith("data: ")) continue;
                    try {
                        var evt = JSON.parse(part.substring(6));
                        if (evt.type === "progress") {
                            confAppendProgress("\u2022 " + evt.message, "sse-line sse-step");
                            requestHint.textContent = evt.message;
                        } else if (evt.type === "done") {
                            finalMessage = evt.message;
                            confAppendProgress("\u2705 " + evt.message.split("\n")[0], "sse-line sse-done");
                            requestHint.textContent = "Review complete";
                            requestHint.style.color = "#2e7d32";
                        } else if (evt.type === "error") {
                            finalMessage = evt.message;
                            confAppendProgress("\u274c " + evt.message, "sse-line sse-error");
                            requestHint.textContent = "Review failed";
                            requestHint.style.color = "#d32f2f";
                        }
                    } catch(e) {}
                }
            }

            chatHistory.push({role: "assistant", text: finalMessage || "Review completed"});
        } catch (err) {
            confAppendProgress("\u2022 Retrying with direct review...", "sse-line sse-info");
            try {
                var res3 = await fetch("/api/chat", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({prompt: msg, history: chatHistory})
                });
                var data3 = await res3.json();
                var resp3 = data3.response || data3.error || "No response";
                confAppendProgress("\u2705 " + resp3.split("\n")[0], "sse-line sse-done");
                chatHistory.push({role: "assistant", text: resp3});
                requestHint.textContent = "Review complete";
                requestHint.style.color = "#2e7d32";
            } catch (err2) {
                confAppendProgress("\u274c Error: " + err2.message, "sse-line sse-error");
                requestHint.textContent = "Error: " + err2.message;
                requestHint.style.color = "#d32f2f";
            }
        }
    })();
});

    // --- Help Modal Handlers ---
    const helpModal = document.getElementById('help-modal');
    const helpBtn = document.getElementById('help-guide-btn');
    const helpCloseBtn = document.getElementById('help-modal-close');
    const helpSidebarBtn = document.getElementById('help-guide-sidebar-btn');

    // Reusable close function for Help Modal
    function closeHelpModal() {
        if (helpModal) {
            helpModal.classList.remove('active');
        }
    }

    // Open handlers
    if (helpBtn && helpModal) {
        helpBtn.addEventListener('click', function() {
            helpModal.classList.add('active');
        });
    }
    if (helpSidebarBtn && helpModal) {
        helpSidebarBtn.addEventListener('click', function() {
            helpModal.classList.add('active');
        });
    }

    // Close handlers
    if (helpCloseBtn) {
        helpCloseBtn.addEventListener('click', closeHelpModal);
    }
    if (helpModal) {
        helpModal.addEventListener('click', function(event) {
            if (event.target === helpModal) {
                closeHelpModal();
            }
        });
    }

    const authSettingsBtn = document.getElementById('auth-settings-btn');
    const authSettingsModal = document.getElementById('auth-settings-modal');   

    if (authSettingsBtn && authSettingsModal) {
        authSettingsBtn.addEventListener('click', function() {
            // Optionally: fillAuthViewFields(); // if you have a function to populate modal fields
            authSettingsModal.classList.add('active');
        });
    }

    const authSettingsCloseBtn = document.getElementById('auth-settings-close');
    if (authSettingsCloseBtn && authSettingsModal) {
        authSettingsCloseBtn.addEventListener('click', function() {
            authSettingsModal.classList.remove('active');
        });
        authSettingsModal.addEventListener('click', function(event) {
            if (event.target === authSettingsModal) {
                authSettingsModal.classList.remove('active');
            }
        });
    }
    // --- Account Setup Modal Logic ---
    const authSettingsView = document.getElementById('auth-settings-view');
    const authSettingsEdit = document.getElementById('auth-settings-edit');
    const editAuthBtn = document.getElementById('edit-auth-btn');
    const saveAuthBtn = document.getElementById('save-auth-btn');
    const cancelAuthBtn = document.getElementById('cancel-auth-btn');

    // Show view state by default
    function showAuthView() {
        authSettingsView.style.display = '';
        authSettingsEdit.style.display = 'none';
    }

    // Show edit state
    function showAuthEdit() {
        authSettingsView.style.display = 'none';
        authSettingsEdit.style.display = '';
    }

    // Edit button
    if (editAuthBtn) {
        editAuthBtn.addEventListener('click', function() {
            showAuthEdit();
        });
    }

    // Cancel button
    if (cancelAuthBtn) {
        cancelAuthBtn.addEventListener('click', function() {
            showAuthView();
        });
    }

    // Save button (demo: just switches back to view)
    if (saveAuthBtn) {
        saveAuthBtn.addEventListener('click', function() {
            // Here you would save the tokens (e.g., via fetch to backend)
            showAuthView();
            // Optionally update masked tokens in view
            document.getElementById('view-confluence-token').textContent = '************';
            document.getElementById('view-github-token').textContent = '************';
        });
    }

    // Close button and click outside to close
    if (authSettingsCloseBtn && authSettingsModal) {
        authSettingsCloseBtn.addEventListener('click', function() {
            authSettingsModal.classList.remove('active');
            showAuthView();
        });
        authSettingsModal.addEventListener('click', function(event) {
            if (event.target === authSettingsModal) {
                authSettingsModal.classList.remove('active');
                showAuthView();
            }
        });
    }

    if (helpSidebarBtn && helpModal) {
        helpSidebarBtn.addEventListener('click', function() {
            helpModal.classList.add('active');
        });
}