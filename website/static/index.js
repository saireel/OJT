const chat = document.getElementById("chat");
const input = document.getElementById("input");
const send = document.getElementById("send");
const stop = document.getElementById("stop");
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
const fastModeToggle = document.getElementById("fast-mode-toggle");
const maxInlineCommentsInput = document.getElementById("max-inline-comments");
const groupSimilarInlineInput = document.getElementById("group-similar-inline");
const prPanelModal = prReviewPanel ? prReviewPanel.querySelector(".panel-modal") : null;
const quickReviewModal = document.getElementById("quick-review-modal");
const quickReviewClose = document.getElementById("quick-review-close");
const quickReviewContinueBtn = document.getElementById("quick-review-continue-btn");
const quickReviewCancelBtn = document.getElementById("quick-review-cancel-btn");
const quickReviewCombined = document.getElementById("quick-review-combined");
const quickReviewConfluence = document.getElementById("quick-review-confluence");
const quickReviewPr = document.getElementById("quick-review-pr");
const confluenceChangeModeBtn = document.getElementById("confluence-change-mode-btn");
const confluenceReviewTitle = document.getElementById("confluence-review-title");
const prChangeModeBtn = document.getElementById("pr-change-mode-btn");
const prReviewTitle = document.getElementById("pr-review-title");
const quickReviewCards = Array.from(document.querySelectorAll(".review-mode-card"));

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
const welcomeMsgTime = document.getElementById("welcome-msg-time");

function formatWelcomeTimestamp() {
    return currentChatTimestamp();
}

function typeWelcomeMessage() {
    if (!welcomeTyping) return;
    const message = "Hi! I am MunnAI, how may I help you today?";
    let index = 0;
    welcomeTyping.textContent = "";
    if (welcomeMsgTime) {
        welcomeMsgTime.textContent = "";
    }

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
        if (welcomeMsgTime) {
            welcomeMsgTime.textContent = formatWelcomeTimestamp();
        }
    }

    step();
}

typeWelcomeMessage();

const USER_AUTH_STORAGE_KEY = "munnai_user_auth";
const USER_AUTH_HISTORY_STORAGE_KEY = "munnai_user_auth_history";
const USER_AUTH_HISTORY_LIMIT = 8;
const USER_CONNECTION_STATUS_KEY = "munnai_connection_status_v1";
const ONBOARDING_SEEN_KEY = "munnai_onboarding_seen_v1";


function normalizeUserAuth(raw) {
    if (!raw || typeof raw !== "object") return {};
    const normalized = {
        confluence_email: (raw.confluence_email || "").trim(),
        confluence_api_token: (raw.confluence_api_token || "").trim(),
        confluence_base_url: (raw.confluence_base_url || "").trim().replace(/\/$/, ""),
        github_owner: (raw.github_owner || "").trim(),
        github_token: (raw.github_token || "").trim(),
        github_base_url: (raw.github_base_url || "").trim().replace(/\/$/, "")
    };

    if (!normalized.github_base_url) {
        normalized.github_base_url = "https://api.github.com";
    }

    Object.keys(normalized).forEach(function(key) {
        if (!normalized[key]) delete normalized[key];
    });

    return normalized;
}

function loadUserAuth() {
    try {
        const raw = localStorage.getItem(USER_AUTH_STORAGE_KEY);
        if (!raw) return {};
        return normalizeUserAuth(JSON.parse(raw));
    } catch (err) {
        return {};
    }
}

function saveUserAuth(auth) {
    const clean = normalizeUserAuth(auth);
    if (!Object.keys(clean).length) {
        localStorage.removeItem(USER_AUTH_STORAGE_KEY);
        return clean;
    }
    localStorage.setItem(USER_AUTH_STORAGE_KEY, JSON.stringify(clean));
    return clean;
}

function loadAuthHistory() {
    try {
        var raw = localStorage.getItem(USER_AUTH_HISTORY_STORAGE_KEY);
        if (!raw) return [];
        var parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return [];
        return parsed
            .map(function(entry) {
                if (!entry || typeof entry !== "object") return null;
                return {
                    id: String(entry.id || ""),
                    saved_at: String(entry.saved_at || ""),
                    auth: normalizeUserAuth(entry.auth || {})
                };
            })
            .filter(function(entry) {
                return entry && entry.id && Object.keys(entry.auth || {}).length;
            });
    } catch (err) {
        return [];
    }
}

function saveAuthHistory(history) {
    if (!Array.isArray(history) || !history.length) {
        localStorage.removeItem(USER_AUTH_HISTORY_STORAGE_KEY);
        return;
    }
    localStorage.setItem(USER_AUTH_HISTORY_STORAGE_KEY, JSON.stringify(history.slice(0, USER_AUTH_HISTORY_LIMIT)));
}

function addAuthHistoryRecord(auth) {
    var clean = normalizeUserAuth(auth);
    if (!Object.keys(clean).length) return;

    var history = loadAuthHistory();
    var serialized = JSON.stringify(clean);
    history = history.filter(function(entry) {
        return JSON.stringify(normalizeUserAuth(entry.auth || {})) !== serialized;
    });

    history.unshift({
        id: String(Date.now()),
        saved_at: new Date().toISOString(),
        auth: clean
    });

    saveAuthHistory(history);
}

function formatAuthHistoryLabel(entry) {
    var dt = entry && entry.saved_at ? new Date(entry.saved_at) : null;
    var when = dt && !isNaN(dt.getTime())
        ? dt.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" })
        : "Unknown time";
    var auth = (entry && entry.auth) || {};
    var owner = auth.github_owner || "no-owner";
    var conf = auth.confluence_email || "no-email";
    return when + " | GH: " + owner + " | Conf: " + conf;
}

var quickReviewMode = "combined";

function getReviewPanelTitle(mode) {
    if (mode === "pr") return "GitHub PR Review";
    if (mode === "confluence") return "Confluence Page Review";
    return "Review Confluence Page with a Pull Request";
}

function syncQuickReviewCardSelection(mode) {
    var selectedMode = mode || quickReviewMode || "combined";
    quickReviewCards.forEach(function(card) {
        var radio = card.querySelector('input[type="radio"][name="quick-review-mode"]');
        if (!radio) return;
        card.classList.toggle("selected", radio.value === selectedMode);
    });
}

function syncConfluenceChecklistVisibility(mode) {
    // Confluence Panel checklists
    var confOnlyList = document.getElementById("confluence-only-checklist-items");
    var confCombinedList = document.getElementById("confluence-combined-checklist-items");
    
    // PR Panel checklists
    var prOnlyList = document.getElementById("pr-only-checklist-items");
    var prCombinedList = document.getElementById("pr-combined-checklist-items");
    
    var legacyList = document.getElementById("confluence-checklist-items");

    // Handle Confluence Panel
    if (confOnlyList && confCombinedList) {
        if (mode === "confluence") {
            confOnlyList.style.display = "block";
            confCombinedList.style.display = "none";
            console.log("Confluence panel: showing only checklist");
        } else {
            // Treat both combined and pr mode as combined checklist in Confluence panel.
            confOnlyList.style.display = "none";
            confCombinedList.style.display = "block";
            console.log("Confluence panel: showing combined checklist");
        }
    }
    
    // Handle PR Panel
    if (prOnlyList && prCombinedList) {
        if (mode === "pr") {
            prOnlyList.style.display = "block";
            prCombinedList.style.display = "none";
        } else {
            prOnlyList.style.display = "none";
            prCombinedList.style.display = "block";
        }
    }

    if (legacyList) {
        legacyList.style.display = "block";
    }
}


function setQuickReviewMode(mode) {
    quickReviewMode = mode || "combined";
    if (confPrLinkGroup) {
        confPrLinkGroup.style.display = quickReviewMode === "combined" ? "" : "none";
    }
    syncConfluenceChecklistVisibility(quickReviewMode);
    if (confluenceReviewTitle) {
        confluenceReviewTitle.textContent = getReviewPanelTitle(quickReviewMode);
    }
    if (prReviewTitle) {
        prReviewTitle.textContent = getReviewPanelTitle("pr");
    }
    if (quickReviewCombined) quickReviewCombined.checked = quickReviewMode === "combined";
    if (quickReviewConfluence) quickReviewConfluence.checked = quickReviewMode === "confluence";
    if (quickReviewPr) quickReviewPr.checked = quickReviewMode === "pr";
    syncQuickReviewCardSelection(quickReviewMode);
}

function openQuickReviewModal(preselectedMode) {
    if (preselectedMode) {
        if (quickReviewCombined) quickReviewCombined.checked = preselectedMode === "combined";
        if (quickReviewConfluence) quickReviewConfluence.checked = preselectedMode === "confluence";
        if (quickReviewPr) quickReviewPr.checked = preselectedMode === "pr";
    }
    syncQuickReviewCardSelection(preselectedMode || quickReviewMode);
    if (quickReviewModal) {
        quickReviewModal.style.display = "flex";
    }
}

function closeQuickReviewModal() {
    if (quickReviewModal) {
        quickReviewModal.style.display = "none";
    }
}
function openConfluencePanelForMode(mode) {
    setQuickReviewMode(mode);

    if (confPanel) {
        confPanel.style.display = "flex";
        mainChat.style.display = "none";
        sidebar.style.display = "none";
    }
    if (prReviewPanel) {
        prReviewPanel.style.display = "none";
    }

    return !!confPanel;
}

function openPrReviewPanelForMode(mode) {
    if (mode) {
        quickReviewMode = mode;
    }
    setQuickReviewMode("pr");
    if (prReviewPanel) {
        prReviewPanel.style.display = "flex";
        mainChat.style.display = "none";
        sidebar.style.display = "none";
    }
    if (confPanel) {
        confPanel.style.display = "none";
    }
}


// Review progress bubble management
function createReviewProgressTracker() {
    let active = null;

    function normalizePreview(message) {
        return String(message || "").replace(/^[.!?⏳✅❌•\s]+/, "").trim() || "Working through the request.";
    }

    function setExpanded(state, expanded) {
        state.details.hidden = !expanded;
        state.summary.setAttribute("aria-expanded", expanded ? "true" : "false");
        state.toggle.textContent = expanded ? "Hide" : "Show";
    }

    function applyState(state, mode) {
        state.mode = mode;
        state.isRunning = mode === "running";
        state.card.dataset.state = mode;
        if (mode === "success") {
            state.icon.textContent = "OK";
        } else if (mode === "error") {
            state.icon.textContent = "!";
        } else {
            state.icon.textContent = "...";
        }
    }

    function buildBubble(title) {
        const bubble = document.createElement("div");
        bubble.className = "msg bot review-progress-msg";
        bubble.innerHTML = '<div class="msg-inner review-progress-card" data-state="running"><div class="msg-time"></div><button type="button" class="review-progress-summary" aria-expanded="false"><span class="review-progress-main"><span class="review-progress-icon">...</span><span class="review-progress-copy"><span class="review-progress-title"></span><span class="review-progress-caption">Preparing request...</span></span></span><span class="review-progress-meta"><span class="review-progress-time">(0s elapsed)</span><span class="review-progress-toggle">Show</span></span></button><div class="review-progress-details" hidden><div class="review-progress-log"></div></div></div>';
        bubble.querySelector(".msg-time").textContent = currentChatTimestamp();
        const state = {
            bubble: bubble,
            card: bubble.querySelector(".review-progress-card"),
            summary: bubble.querySelector(".review-progress-summary"),
            icon: bubble.querySelector(".review-progress-icon"),
            title: bubble.querySelector(".review-progress-title"),
            caption: bubble.querySelector(".review-progress-caption"),
            elapsed: bubble.querySelector(".review-progress-time"),
            toggle: bubble.querySelector(".review-progress-toggle"),
            details: bubble.querySelector(".review-progress-details"),
            log: bubble.querySelector(".review-progress-log"),
            startTime: Date.now(),
            isRunning: true,
            mode: "running"
        };

        state.title.textContent = title || "Review in progress...";
        state.summary.addEventListener("click", function() {
            setExpanded(state, state.details.hidden);
        });

        applyState(state, "running");
        setExpanded(state, false);
        chat.appendChild(bubble);
        chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
        return state;
    }

    function ensureActive() {
        if (!active || !active.bubble.isConnected) {
            active = buildBubble("Review in progress...");
        }
        return active;
    }

    return {
        show: function(title, initialMessage) {
            active = buildBubble(title || "Review in progress...");
            if (initialMessage) {
                this.addLog(initialMessage);
            }
            this.updateTimer();
        },
        hide: function() {
            if (!active) return;
            setExpanded(active, false);
        },
        addLog: function(message, type) {
            const state = ensureActive();
            const entry = document.createElement("div");
            entry.className = "review-progress-entry" + (type ? " " + type : "");
            entry.innerHTML = renderSimpleMarkdown(String(message || ""));
            state.log.appendChild(entry);
            state.log.scrollTop = state.log.scrollHeight;
            state.caption.textContent = normalizePreview(message);
            if (type === "error") {
                setExpanded(state, true);
            }
            chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
        },
        updateTimer: function() {
            if (!active || !active.isRunning) return;
            const elapsed = Math.floor((Date.now() - active.startTime) / 1000);
            active.elapsed.textContent = "(" + elapsed + "s elapsed)";
        },
        setTitle: function(title) {
            const state = ensureActive();
            state.title.textContent = title || "Review in progress...";
        },
        setState: function(mode) {
            const state = ensureActive();
            applyState(state, mode || "running");
            if (mode === "error") {
                setExpanded(state, true);
            }
        }
    };
}

const reviewProgress = createReviewProgressTracker();

setInterval(function() {
    reviewProgress.updateTimer();
}, 1000);

function getDraftUserAuthFromInputs() {
    const readValue = function(ids) {
        const idList = Array.isArray(ids) ? ids : [ids];
        for (var i = 0; i < idList.length; i += 1) {
            const el = document.getElementById(idList[i]);
            if (el && typeof el.value === 'string') {
                return el.value;
            }
        }
        return '';
    };
    const draft = {
        confluence_email: readValue(['edit-confluence-email']),
        confluence_api_token: readValue(['edit-confluence-token']),
        confluence_base_url: readValue(['edit-confluence-base-url']),
        github_owner: readValue(['edit-github-owner']),
        github_token: readValue(['edit-github-token']),
        github_base_url: readValue(['edit-github-base-url'])
    };
    return normalizeUserAuth(draft);
}

function getUserAuthPayload() {
    return normalizeUserAuth(Object.assign({}, loadUserAuth(), getDraftUserAuthFromInputs()));
}

function withUserAuthPayload(payload) {
    return Object.assign({}, payload, { user_auth: getUserAuthPayload() });
}

function maskSecret(value) {
    if (!value) return "Not set";
    if (value.length <= 8) return "*".repeat(value.length);
    const visible = value.slice(-4);
    return "*".repeat(Math.max(8, value.length - 4)) + visible;
}

function currentChatTimestamp() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function isErrorLikeText(text) {
    var t = String(text || "").trim().toLowerCase();
    if (!t) return false;
    return t.startsWith("error") || t.startsWith("review failed") || t.includes("invalid url") || t.includes("not configured") || t.includes("failed:");
}

function extractErrorMessage(data, fallback) {
    if (data && typeof data.error === "string" && data.error.trim()) return data.error.trim();
    if (data && typeof data.response === "string" && isErrorLikeText(data.response)) return data.response.trim();
    var fb = String(fallback || "").trim();
    return fb || "Request failed";
}

function isAuthErrorLikeText(text) {
    var t = String(text || "").toLowerCase();
    return t.includes("401") || t.includes("403") || t.includes("unauthorized") || t.includes("forbidden") || t.includes("token expired") || t.includes("expired token") || t.includes("invalid token") || t.includes("invalid credentials") || t.includes("not configured") || t.includes("authentication failed");
}

function toActionableErrorMessage(text) {
    var msg = String(text || "").trim();
    if (!msg) return "Request failed";
    if (isAuthErrorLikeText(msg) && msg.toLowerCase().indexOf("account setup") === -1) {
        return msg + "\n\nAction: Open Account Setup, refresh/save credentials, then click Test Connection.";
    }
    return msg;
}

function parseGithubApiBaseUrlFromPrLink(link) {
    var text = String(link || "").trim();
    if (!text) return "https://api.github.com";
    var urlMatch = text.match(/^https?:\/\/([^\/]+)\//i);
    if (!urlMatch) return "https://api.github.com";
    var host = urlMatch[1].toLowerCase();
    if (host === "github.com" || host.endsWith(".github.com")) return "https://api.github.com";
    var scheme = text.toLowerCase().startsWith("http://") ? "http://" : "https://";
    return scheme + urlMatch[1] + "/api/v3";
}

function parseConfluenceBaseUrlFromPageLink(link) {
    var text = String(link || "").trim();
    if (!text) return "";
    var m = text.match(/^(https?:\/\/[^\/]+)(\/wiki)?/i);
    if (!m) return "";
    if (m[2]) return m[1] + "/wiki";
    if (/atlassian\.net/i.test(m[1])) return m[1] + "/wiki";
    return m[1];
}

function escapeHtml(text) {
    return String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function formatInlineMarkdown(text) {
    let out = String(text || "");
    out = out.replace(/`([^`]+?)`/g, "<code>$1</code>");
    out = out.replace(/\*\*([^*][\s\S]*?)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");
    out = out.replace(/__([^_][\s\S]*?)__/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^_])_([^_\n]+?)_(?!_)/g, "$1<em>$2</em>");
    return out;
}

function renderSimpleMarkdown(text) {
    const escaped = escapeHtml(text);
    const lines = escaped.split(/\r?\n/);
    const html = [];
    let inList = false;

    lines.forEach(function(line) {
        const listMatch = line.match(/^\s*[-*]\s+(.+)/);
        if (listMatch) {
            if (!inList) {
                html.push("<ul>");
                inList = true;
            }
            html.push("<li>" + formatInlineMarkdown(listMatch[1]) + "</li>");
            return;
        }

        if (inList) {
            html.push("</ul>");
            inList = false;
        }

        if (!line.trim()) {
            html.push("<br>");
            return;
        }

        html.push("<div>" + formatInlineMarkdown(line) + "</div>");
    });

    if (inList) {
        html.push("</ul>");
    }

    return html.join("");
}

function createUserMessageBubble(text) {
    const bubble = document.createElement("div");
    bubble.className = "msg user";
    bubble.innerHTML = '<div class="msg-inner"><div class="msg-time"></div><div class="msg-body"></div></div>';
    bubble.querySelector(".msg-time").textContent = currentChatTimestamp();
    bubble.querySelector(".msg-body").textContent = text || "";
    chat.appendChild(bubble);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
    return bubble;
}

function createStreamingBubble() {
    const bubble = document.createElement("div");
    bubble.className = "msg bot";
    bubble.innerHTML = `<div class="msg-inner"><div class="msg-time">${currentChatTimestamp()}</div><div class="msg-body"><span class="stream-text"></span><span class="cursor">|</span></div></div>`;
    chat.appendChild(bubble);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
    return { textEl: bubble.querySelector(".stream-text"), cursorEl: bubble.querySelector(".cursor") };
}

function createAssistantMessageBubble(text) {
    const bubble = document.createElement("div");
    bubble.className = "msg bot";
    bubble.innerHTML = '<div class="msg-inner"><div class="msg-time"></div><div class="msg-body"></div></div>';
    bubble.querySelector(".msg-time").textContent = currentChatTimestamp();
    bubble.querySelector(".msg-body").innerHTML = renderSimpleMarkdown(String(text || ""));
    chat.appendChild(bubble);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
    return bubble;
}

function getLocalFastSmalltalkReply(text) {
    const normalized = String(text || "")
        .toLowerCase()
        .replace(/[^a-z0-9\s?!.,]/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .replace(/[?!.,]+/g, "")
        .trim();

    if (!normalized) return null;

    const quickGreetings = new Set(["hi", "hello", "hey", "yo", "hii", "hey there", "hello there", "hi there"]);
    const greetingWords = new Set(["hi", "hello", "hey", "yo", "hii"]);
    const greetingTargets = new Set(["there", "munnai", "assistant", "ai"]);
    const tokens = normalized.split(" ").filter(Boolean);

    if (quickGreetings.has(normalized)) {
        return "Hi! I am ready. Share a PR link, Confluence page link, or tell me what you want reviewed.";
    }
    if (tokens.length > 0 && tokens.length <= 3 && greetingWords.has(tokens[0])) {
        if (tokens.length === 1 || greetingTargets.has(tokens[1])) {
            return "Hi! I am ready. Share a PR link, Confluence page link, or tell me what you want reviewed.";
        }
    }
    if (["thanks", "thank you", "ty", "tnx"].includes(normalized)) {
        return "You are welcome. I can help with PR reviews, Confluence checks, and account setup too.";
    }
    if (["ok", "okay", "k", "cool", "nice"].includes(normalized)) {
        return "Great. Send the next task when you are ready.";
    }

    return null;
}

function streamText(textEl, cursorEl, text, speed = 8) {
    let i = 0;
    const fullText = String(text || "");

    function step() {
        if (i < fullText.length) {
            textEl.textContent += fullText.charAt(i);
            i++;
            chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
            setTimeout(step, speed);
            return;
        }

        textEl.innerHTML = renderSimpleMarkdown(fullText);
        if (cursorEl) cursorEl.remove();
    }

    step();
}
function createTypingIndicator() {
    const bubble = document.createElement("div");
    bubble.className = "msg bot typing";
    bubble.innerHTML = `<div class="msg-inner"><div class="msg-body"><div class="typing-indicator"><span></span><span></span><span></span></div></div></div>`;
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
            <div class="msg-time">${currentChatTimestamp()}</div>
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

// --- PR Review Panel ---
prReviewBtn.addEventListener("click", function() {
    openPrReviewPanelForMode("pr");
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
    var parsed = parseGithubPrLink(prLinkInput.value.trim());
    if (parsed) {
        prDisplay.textContent = parsed.owner + "/" + parsed.repo + "#" + parsed.prNum;
        prInfo.style.display = "block";
    } else {
        showUiAlert("Invalid PR link. Use: https://github.com/owner/repo/pull/123 or owner/repo#123");
    }
});

// Select All / Deselect All for checklist
if (selectAllBtn && checklistItems) {
    selectAllBtn.addEventListener("click", function() {
        checklistItems.querySelectorAll("input[type=checkbox]").forEach(function(cb) { cb.checked = true; });
    });
}
if (deselectAllBtn && checklistItems) {
    deselectAllBtn.addEventListener("click", function() {
        checklistItems.querySelectorAll("input[type=checkbox]").forEach(function(cb) { cb.checked = false; });
    });
}


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

if (fastModeToggle) {
    // Strict/high-confidence mode is always on.
    fastModeToggle.checked = false;
    fastModeToggle.disabled = true;
    fastModeToggle.title = "Strict/high-confidence mode is enforced.";
}


// Start Review - builds prompt and sends to chat
startReviewBtn.addEventListener("click", function() {
    var prLink = prLinkInput.value.trim();
    if (!prLink) { showUiAlert("Please enter a PR link"); return; }
    var parsedPr = parseGithubPrLink(prLink);
    if (!parsedPr) { showUiAlert("Invalid PR link format. Use a URL or owner/repo#123"); return; }

    var outputs = [];
    document.querySelectorAll("#output-options .output-item").forEach(function(item) {
        if (item.querySelector("input").checked) {
            outputs.push(item.querySelector("span").textContent.trim());
        }
    });

    if (outputs.length === 0) { showUiAlert("Please select at least one expected output type"); return; }

    var items = checklistItems.querySelectorAll(".check-item");
    // AFTER — replace with this:
    var activeChecklist = document.querySelector(
        '#pr-only-checklist-items:not([style*="display:none"]), #pr-combined-checklist-items:not([style*="display:none"]), #confluence-only-checklist-items:not([style*="display:none"]), #confluence-combined-checklist-items:not([style*="display:none"])'
    );
    var checklist = [];
    if (activeChecklist) {
        activeChecklist.querySelectorAll(".check-item").forEach(function(item) {
            if (item.querySelector("input").checked) {
                checklist.push(item.querySelector("span").textContent);
            }
        });
    }
    var owner = parsedPr.owner, repo = parsedPr.repo, prNum = parsedPr.prNum;
    var githubBaseUrl = parseGithubApiBaseUrlFromPrLink(prLink);
    var fastMode = false;
    var maxInlineComments = 12;
    var groupSimilarInline = true;
    if (maxInlineCommentsInput) maxInlineCommentsInput.value = String(maxInlineComments);
    if (groupSimilarInlineInput) groupSimilarInlineInput.checked = true;

    var msg = "Review this GitHub PR: https://github.com/" + owner + "/" + repo + "/pull/" + prNum;
    msg += "\n\nExpected output:\n- " + outputs.join("\n- ");

    if (checklist.length > 0) {
        msg += "\n\nChecklist items to review:\n- " + checklist.join("\n- ");
    }

    msg += "\n\nReview tuning:";
    msg += "\n- Operating mode: Strict / High-Confidence";
    msg += "\n- Max inline comments: " + maxInlineComments;
    msg += "\n- Group similar inline: " + (groupSimilarInline ? "Yes" : "No");

    prReviewPanel.style.display = "none";
    mainChat.style.display = "flex";
    sidebar.style.display = "";
    prLinkInput.value = "";
    prInfo.style.display = "none";

    createUserMessageBubble(msg);

    chatHistory.push({role: "user", text: msg});
    requestHint.textContent = "Sending request...";
    requestHint.style.color = "#666";

    reviewProgress.show("GitHub PR review", "Starting review...");

    (async function() {
        try {
            var res = await fetch("/api/review-stream", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(withUserAuthPayload({prompt: msg, history: chatHistory, checklist: checklist, outputs: outputs, fast_mode: fastMode, max_inline_comments: maxInlineComments, group_similar_inline: groupSimilarInline, github_base_url: githubBaseUrl}))
            });

            if (!res.ok || !res.body) {
                reviewProgress.addLog("Streaming unavailable, using direct review...");
                var res2 = await fetch("/api/chat", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(withUserAuthPayload({prompt: msg, history: chatHistory}))
                });
                var data2 = await res2.json();
                var resp = data2.response || data2.error || "No response";
                var fallbackFailed = !res2.ok || !!data2.error || isErrorLikeText(resp);
                if (fallbackFailed) {
                    var fallbackErr = toActionableErrorMessage(extractErrorMessage(data2, resp));
                    markConnectionStatusFromError(fallbackErr);
                    reviewProgress.addLog("Error: " + fallbackErr, "error");
                    reviewProgress.setTitle("Review failed");
                    reviewProgress.setState("error");
                    requestHint.textContent = "Review failed";
                    requestHint.style.color = "#d32f2f";
                    chatHistory.push({role: "assistant", text: fallbackErr});
                    createAssistantMessageBubble(fallbackErr);
                    return;
                }
                reviewProgress.addLog("OK " + resp.split("\n")[0], "success");
                reviewProgress.setTitle("Review complete");
                reviewProgress.setState("success");
                reviewProgress.hide();
                chatHistory.push({role: "assistant", text: resp});
                requestHint.textContent = "Review complete";
                requestHint.style.color = "#2e7d32";
                return;
            }

            var reader = res.body.getReader();
            var decoder = new TextDecoder();
            var buffer = "";
            var finalMessage = "";
            var sawDoneEvent = false;
            var sawErrorEvent = false;

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
                            reviewProgress.addLog(evt.message);
                            requestHint.textContent = evt.message;
                            requestHint.style.color = "#666";
                        } else if (evt.type === "done") {
                            finalMessage = evt.message || "";
                            sawDoneEvent = true;
                            if (!sawErrorEvent && !isErrorLikeText(finalMessage)) {
                                reviewProgress.addLog(finalMessage || "Review completed.", "success");
                                reviewProgress.setTitle("Review complete");
                                reviewProgress.setState("success");
                                reviewProgress.hide();
                                requestHint.textContent = "Review complete";
                                requestHint.style.color = "#2e7d32";
                            } else {
                                finalMessage = toActionableErrorMessage(finalMessage || "Review failed during streaming.");
                                sawErrorEvent = true;
                                markConnectionStatusFromError(finalMessage);
                                reviewProgress.addLog(finalMessage, "error");
                                reviewProgress.setTitle("Review failed");
                                reviewProgress.setState("error");
                                requestHint.textContent = "Review failed";
                                requestHint.style.color = "#d32f2f";
                            }
                        } else if (evt.type === "error") {
                            finalMessage = toActionableErrorMessage(evt.message);
                            sawErrorEvent = true;
                            markConnectionStatusFromError(finalMessage);
                            reviewProgress.addLog(finalMessage, "error");
                            reviewProgress.setTitle("Review failed");
                            reviewProgress.setState("error");
                            requestHint.textContent = "Review failed";
                            requestHint.style.color = "#d32f2f";
                        }
                    } catch (e) {}
                }
            }

            if (!finalMessage) {
                finalMessage = "Review failed: No completion payload returned.";
                reviewProgress.addLog(finalMessage, "error");
                reviewProgress.setTitle("Review failed");
                reviewProgress.setState("error");
                requestHint.textContent = "Review failed";
                requestHint.style.color = "#d32f2f";
            }
            if (sawErrorEvent && !sawDoneEvent) {
                finalMessage = toActionableErrorMessage(finalMessage);
            }
            chatHistory.push({role: "assistant", text: finalMessage});
        } catch (err) {
            reviewProgress.addLog("Retrying with direct review...");
            try {
                var res3 = await fetch("/api/chat", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(withUserAuthPayload({prompt: msg, history: chatHistory}))
                });
                var data3 = await res3.json();
                var resp3 = data3.response || data3.error || "No response";
                var resp3Failed = !res3.ok || !!data3.error || isErrorLikeText(resp3);
                if (resp3Failed) {
                    var err3 = toActionableErrorMessage(extractErrorMessage(data3, resp3));
                    markConnectionStatusFromError(err3);
                    reviewProgress.addLog(err3, "error");
                    reviewProgress.setTitle("Review failed");
                    reviewProgress.setState("error");
                    chatHistory.push({role: "assistant", text: err3});
                    requestHint.textContent = "Review failed";
                    requestHint.style.color = "#d32f2f";
                } else {
                    reviewProgress.addLog("OK " + resp3.split("\n")[0], "success");
                    reviewProgress.setTitle("Review complete");
                    reviewProgress.setState("success");
                    reviewProgress.hide();
                    chatHistory.push({role: "assistant", text: resp3});
                    requestHint.textContent = "Review complete";
                    requestHint.style.color = "#2e7d32";
                }
            } catch (err2) {
                var err2Msg = toActionableErrorMessage(err2.message);
                markConnectionStatusFromError(err2Msg);
                reviewProgress.addLog("Error: " + err2Msg, "error");
                reviewProgress.setTitle("Review failed");
                reviewProgress.setState("error");
                requestHint.textContent = "Error: " + err2Msg;
                requestHint.style.color = "#d32f2f";
            }
        }
    })();
});

// --- Original Chat ---
var chatHistory = [];
let activeChatRequest = null;
let chatRequestSeq = 0;

function setStopButtonState(isActive) {
    if (!stop) return;
    stop.disabled = !isActive;
}

send.addEventListener("click", async function() {
    var msg = input.value.trim();
    if (!msg) return;

    if (activeChatRequest && activeChatRequest.controller && !activeChatRequest.controller.signal.aborted) {
        activeChatRequest.controller.abort();
    }

    const requestId = ++chatRequestSeq;
    const requestController = new AbortController();
    activeChatRequest = { id: requestId, controller: requestController };
    setStopButtonState(true);
    const isStaleRequest = function() {
        return !activeChatRequest || activeChatRequest.id !== requestId;
    };

    input.value = "";

    createUserMessageBubble(msg);
    chatHistory.push({role: "user", text: msg});

    requestHint.textContent = "Processing...";
    requestHint.style.color = "#666";

    const localFastReply = getLocalFastSmalltalkReply(msg);
    if (localFastReply) {
        createAssistantMessageBubble(localFastReply);
        chatHistory.push({role: "assistant", text: localFastReply});
        requestHint.textContent = "Ready for next request";
        requestHint.style.color = "#999";
        if (activeChatRequest && activeChatRequest.id === requestId) {
            activeChatRequest = null;
            setStopButtonState(false);
        }
        return;
    }

    const typingBubble = createTypingBubble();
    let progressBubble = null;
    const cleanupTransientBubbles = function() {
        if (progressBubble) progressBubble.remove();
        removeTypingBubble(typingBubble);
    };

    try {
        var sseRes = await fetch("/api/chat-stream", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(withUserAuthPayload({prompt: msg, history: chatHistory})),
            signal: requestController.signal,
        });

        if (!sseRes.ok || !sseRes.body) {
            throw new Error("SSE unavailable");
        }

        const sseReader = sseRes.body.getReader();
        const sseDec = new TextDecoder();
        let sseBuf = "";
        let finalResponse = null;
        let finalDetected = null;

        while (true) {
            const { value, done } = await sseReader.read();
            if (isStaleRequest()) {
                try { await sseReader.cancel(); } catch (e) {}
                cleanupTransientBubbles();
                return;
            }
            if (done) break;
            sseBuf += sseDec.decode(value, { stream: true });
            const parts = sseBuf.split("\n\n");
            sseBuf = parts.pop();
            for (const part of parts) {
                if (!part.startsWith("data: ")) continue;
                let evt;
                try { evt = JSON.parse(part.slice(6)); } catch(e) { continue; }
                if (evt.type === "progress") {
                    removeTypingBubble(typingBubble);
                    if (!progressBubble) {
                        progressBubble = document.createElement("div");
                        progressBubble.className = "msg bot typing-bubble";
                        progressBubble.innerHTML = '<div class="msg-inner"><div class="msg-body" style="font-style:italic;opacity:0.7;font-size:0.9em;"></div></div>';
                        chat.appendChild(progressBubble);
                        chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
                    }
                    progressBubble.querySelector(".msg-body").textContent = evt.message;
                    requestHint.textContent = evt.message;
                } else if (evt.type === "done") {
                    try {
                        const d = JSON.parse(evt.message);
                        finalResponse = d.response || "No response";
                        finalDetected = d.detected;
                    } catch(e) {
                        finalResponse = evt.message;
                    }
                } else if (evt.type === "error") {
                    finalResponse = evt.message;
                }
            }
        }

        if (isStaleRequest()) {
            cleanupTransientBubbles();
            return;
        }
        cleanupTransientBubbles();

        var response = finalResponse || "No response";
        var data = { response, detected: finalDetected };

        createAssistantMessageBubble(response);
        chatHistory.push({role: "assistant", text: response});

        if (isErrorLikeText(response)) {
            requestHint.textContent = "Review failed";
            requestHint.style.color = "#d32f2f";
        } else {
            requestHint.textContent = "Ready for next request";
            requestHint.style.color = "#999";
        }

    } catch (err) {
        if (isStaleRequest()) {
            cleanupTransientBubbles();
            return;
        }
        cleanupTransientBubbles();

        if (err && (err.name === "AbortError" || /abort/i.test(String(err.message || "")))) {
            requestHint.textContent = "Interrupted by new request";
            requestHint.style.color = "#999";
            return;
        }

        const typingBubble2 = createTypingBubble();
        try {
            var res2 = await fetch("/api/chat", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(withUserAuthPayload({prompt: msg, history: chatHistory})),
                signal: requestController.signal,
            });
            if (isStaleRequest()) {
                removeTypingBubble(typingBubble2);
                return;
            }
            var data2 = await res2.json();
            var response2 = data2.response || data2.error || "No response";
            removeTypingBubble(typingBubble2);
            createAssistantMessageBubble(response2);
            chatHistory.push({role: "assistant", text: response2});
            if (!res2.ok || data2.error || isErrorLikeText(response2)) {
                requestHint.textContent = "Review failed";
                requestHint.style.color = "#d32f2f";
            } else {
                requestHint.textContent = "Ready for next request";
                requestHint.style.color = "#999";
            }
            return;
        } catch(err2) {
            removeTypingBubble(typingBubble2);
            if (isStaleRequest()) return;
            var _err = err2 || err;

            var errorMsg = document.createElement("div");
            errorMsg.className = "msg bot";
            errorMsg.innerHTML = `
                <div class="msg-inner" style="border-color:#d32f2f;color:#d32f2f;">
                    <div class="msg-time">${currentChatTimestamp()}</div>
                    <div class="msg-body">Error: ${((_err && _err.message) || "Unknown error").replace(/</g,"&lt;")}</div>
                </div>
            `;
            chat.appendChild(errorMsg);

            requestHint.textContent = "Error occurred";
            requestHint.style.color = "#d32f2f";
        }
    } finally {
        if (activeChatRequest && activeChatRequest.id === requestId) {
            activeChatRequest = null;
            setStopButtonState(false);
        }
    }
});

input.addEventListener("keydown", function(e) {
    if (e.ctrlKey && e.key === "Enter") { send.click(); }
});


if (stop) {
    stop.addEventListener("click", function() {
        if (activeChatRequest && activeChatRequest.controller && !activeChatRequest.controller.signal.aborted) {
            activeChatRequest.controller.abort();
            requestHint.textContent = "Stopped";
            requestHint.style.color = "#999";
        }
        setStopButtonState(false);
    });
}

// --- Confluence Review Panel ---
var confPanel = document.getElementById("confluence-review-panel");
var confReviewBtn = document.getElementById("quick-review-btn");
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
var confStartPrBtn = document.getElementById("start-confluence-pr-review-btn");
var confStartPrOnlyBtn = document.getElementById("start-confluence-pr-only-btn");
var confPrLinkInput = document.getElementById("confluence-pr-link-input");
var confPrLinkGroup = document.getElementById("confluence-pr-link-group");
var confLinkInput = document.getElementById("confluence-link-input");
var confParseBtn = document.getElementById("parse-confluence-btn");
var confInfo = document.getElementById("confluence-info");
var confDisplay = document.getElementById("confluence-display");
var confDocType = document.getElementById("confluence-doc-type");
function getActiveConfluenceChecklistContainer() {
    var onlyList = document.getElementById("confluence-only-checklist-items");
    var combinedList = document.getElementById("confluence-combined-checklist-items");
    var legacyList = document.getElementById("confluence-checklist-items");

    if (quickReviewMode === "confluence") {
        return onlyList || legacyList || combinedList;
    }
    // For combined or pr mode, always prefer the combined checklist.
    return combinedList || legacyList || onlyList;
}
function getUiAlertHost() {
    var existing = document.getElementById("ui-alert-host");
    if (existing) {
        return existing;
    }

    var host = document.createElement("div");
    host.id = "ui-alert-host";
    host.className = "ui-alert-host";
    document.body.appendChild(host);
    return host;
}

function renderUiAlert(message) {
    var host = getUiAlertHost();
    var alertNode = document.createElement("div");
    alertNode.className = "ui-alert-toast";
    alertNode.setAttribute("role", "alert");
    alertNode.textContent = String(message || "Something went wrong.");

    host.appendChild(alertNode);

    requestAnimationFrame(function() {
        alertNode.classList.add("show");
    });

    window.setTimeout(function() {
        alertNode.classList.remove("show");
        window.setTimeout(function() {
            if (alertNode.parentNode) {
                alertNode.parentNode.removeChild(alertNode);
            }
        }, 220);
    }, 4200);
}

function showUiAlert(message) {
    var text = String(message || "Something went wrong.");
    renderUiAlert(text);

    if (requestHint) {
        requestHint.textContent = text;
        requestHint.style.color = "#d32f2f";
    }

    if (typeof createAssistantMessageBubble === "function") {
        createAssistantMessageBubble(text);
    }
}
confReviewBtn.addEventListener("click", function() {
    openQuickReviewModal(quickReviewMode || "combined");
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
if (confluenceChangeModeBtn) {
    confluenceChangeModeBtn.addEventListener("click", function() {
        openQuickReviewModal(quickReviewMode || "combined");
    });
}
if (prChangeModeBtn) {
    prChangeModeBtn.addEventListener("click", function() {
        openQuickReviewModal("pr");
    });
}
if (quickReviewClose) {
    quickReviewClose.addEventListener("click", closeQuickReviewModal);
}
if (quickReviewCancelBtn) {
    quickReviewCancelBtn.addEventListener("click", closeQuickReviewModal);
}
if (quickReviewModal) {
    quickReviewModal.addEventListener("click", function(event) {
        if (event.target === quickReviewModal) {
            closeQuickReviewModal();
        }
    });
}
if (quickReviewCards.length) {
    quickReviewCards.forEach(function(card) {
        var radio = card.querySelector('input[type="radio"][name="quick-review-mode"]');
        if (radio) {
            radio.addEventListener("change", function() {
                syncQuickReviewCardSelection(radio.value);
            });
        }
    });
}
if (quickReviewContinueBtn) {
    quickReviewContinueBtn.addEventListener("click", function() {
        var selected = document.querySelector('input[name="quick-review-mode"]:checked');
        var mode = selected ? selected.value : "combined";

        if (mode === "pr") {
            openPrReviewPanelForMode(mode);
            closeQuickReviewModal();
            return;
        }

        var opened = openConfluencePanelForMode(mode);
        if (opened) {
            closeQuickReviewModal();
            return;
        }

        showUiAlert("Could not open the Confluence review panel. Reload the page and try again.");
    });
}
setQuickReviewMode("combined");
if (confluenceReviewTitle) {
    confluenceReviewTitle.textContent = getReviewPanelTitle("combined");
}
if (prReviewTitle) {
    prReviewTitle.textContent = getReviewPanelTitle("pr");
}


function parseConfluencePageLink(link) {
    var text = String(link || "").trim();
    if (!text) return null;
    var pageMatch = text.match(/(?:\/pages\/|pageId=)(\d+)/i);
    if (pageMatch) {
        return {
            pageId: pageMatch[1],
            source: text
        };
    }
    if (/^\d+$/.test(text)) {
        return {
            pageId: text,
            source: text
        };
    }
    return null;
}

// Parse Confluence link (and PR link when combined mode is selected)
confParseBtn.addEventListener("click", function() {
    var confParsed = parseConfluencePageLink(confLinkInput.value);
    if (!confParsed) {
        showUiAlert("Could not parse Confluence page. Use a URL containing /pages/<id> or ?pageId=<id>, or enter a numeric page ID.");
        return;
    }

    var parseMessage = "Page ID: " + confParsed.pageId;
    var mode = quickReviewMode || "combined";

    if (mode === "combined") {
        var prParsed = parseGithubPrLink(confPrLinkInput ? confPrLinkInput.value : "");
        if (!prParsed) {
            showUiAlert("Combined mode requires a valid GitHub PR URL or owner/repo#123.");
            return;
        }
        parseMessage += " | PR: " + prParsed.owner + "/" + prParsed.repo + "#" + prParsed.prNum;
    }

    confDisplay.textContent = parseMessage;
    confInfo.style.display = "block";
});

const confluenceSelectAllBtn = document.getElementById('confluence-select-all-btn');
if (confluenceSelectAllBtn) {
    confluenceSelectAllBtn.addEventListener('click', () => {
        const activeList = getActiveConfluenceChecklistContainer();
        if (activeList) activeList.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
    });
}

const confluenceDeselectAllBtn = document.getElementById('confluence-deselect-all-btn');
if (confluenceDeselectAllBtn) {
    confluenceDeselectAllBtn.addEventListener('click', () => {
        const activeList = getActiveConfluenceChecklistContainer();
        if (activeList) activeList.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    });
}

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

function parseGithubPrLink(link) {
    var text = String(link || "").trim();
    if (!text) return null;
    var urlMatch = text.match(/github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)/i);
    if (urlMatch) {
        return { owner: urlMatch[1], repo: urlMatch[2], prNum: urlMatch[3], url: "https://github.com/" + urlMatch[1] + "/" + urlMatch[2] + "/pull/" + urlMatch[3] };
    }
    var shorthandMatch = text.match(/^([^\s/#]+)\/([^\s/#]+)#(\d+)$/);
    if (shorthandMatch) {
        return { owner: shorthandMatch[1], repo: shorthandMatch[2], prNum: shorthandMatch[3], url: "https://github.com/" + shorthandMatch[1] + "/" + shorthandMatch[2] + "/pull/" + shorthandMatch[3] };
    }
    return null;
}


// Start Confluence Review
confStartBtn.addEventListener("click", function() {
    var link = confLinkInput.value.trim();
    var confluenceBaseUrl = parseConfluenceBaseUrlFromPageLink(link);
    if (!link) { showUiAlert("Please enter a Confluence page URL or ID"); return; }

    var parsedPage = parseConfluencePageLink(link);
    if (!parsedPage) {
        showUiAlert("Could not parse page ID from input. Use /pages/<id>, ?pageId=<id>, or a numeric page ID.");
        return;
    }
    var pageId = parsedPage.pageId;

    var docType = confDocType.value;
    var checklist = [];
    var activeConfluenceChecklist = getActiveConfluenceChecklistContainer();
    if (activeConfluenceChecklist) {
        activeConfluenceChecklist.querySelectorAll(".check-item").forEach(function(item) {
            if (item.querySelector("input").checked) {
                checklist.push(item.querySelector("span").textContent);
            }
        });
    }

    if (checklist.length === 0) { showUiAlert("Please select at least one checklist item"); return; }

    var outputs = [];
    document.querySelectorAll("#confluence-output-options .output-item").forEach(function(item) {
        if (item.querySelector("input").checked) {
            outputs.push(item.querySelector("span").textContent.trim());
        }
    });
    if (outputs.length === 0) { showUiAlert("Please select at least one expected output type"); return; }

    var currentMode = quickReviewMode || "combined";
    var prLink = confPrLinkInput ? confPrLinkInput.value.trim() : "";
    var prMatch = null;
    if (currentMode === "combined") {
        if (!prLink) { showUiAlert("Please enter a GitHub PR URL or ID"); return; }
        prMatch = parseGithubPrLink(prLink);
        if (!prMatch) { showUiAlert("Could not parse GitHub PR link. Use a GitHub PR URL or owner/repo#123."); return; }
    }

    var msg = "Review this Confluence page: " + link;
    if (currentMode === "combined" && prMatch) {
        msg += "\n\nReview this GitHub PR: " + prMatch.url;
        msg += "\n\nReview type: document and code";
    } else {
        msg += "\n\nReview type: confluence page review";
    }
    if (docType) {
        msg += "\n\nDocument type: " + docType;
    }
    msg += "\n\nExpected output:\n- " + outputs.join("\n- ");
    if (checklist.length > 0) {
        msg += "\n\nChecklist items to review:\n- " + checklist.join("\n- ");
    }

    confPanel.style.display = "none";
    mainChat.style.display = "flex";
    sidebar.style.display = "";

    createUserMessageBubble(msg);
    chatHistory.push({role: "user", text: msg});
    requestHint.textContent = currentMode === "combined" ? "Sending combined review request..." : "Sending request...";
    requestHint.style.color = "#666";
    reviewProgress.show(currentMode === "combined" ? "Combined document/code review" : "Confluence page review", currentMode === "combined" ? "Starting combined review..." : "Starting Confluence review...");

    (async function() {
        try {
            if (currentMode === "combined") {
                var combinedRes = await fetch("/api/chat", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(withUserAuthPayload({
                        prompt: msg,
                        history: chatHistory,
                        review_type: "document and code",
                        doc_type: docType,
                        checklist: checklist,
                        outputs: outputs,
                        confluence_checklist_page_id: pageId,
                        confluence_base_url: confluenceBaseUrl
                    }))
                });
                var combinedData = await combinedRes.json();
                var combinedResp = combinedData.response || combinedData.error || "No response";
                var combinedFailed = !combinedRes.ok || !!combinedData.error || isErrorLikeText(combinedResp);
                if (combinedFailed) {
                    var combinedErr = toActionableErrorMessage(extractErrorMessage(combinedData, combinedResp));
                    markConnectionStatusFromError(combinedErr);
                    reviewProgress.addLog("Error: " + combinedErr, "error");
                    reviewProgress.setTitle("Review failed");
                    reviewProgress.setState("error");
                    requestHint.textContent = "Review failed";
                    requestHint.style.color = "#d32f2f";
                    chatHistory.push({role: "assistant", text: combinedErr});
                    createAssistantMessageBubble(combinedErr);
                    return;
                }
                reviewProgress.addLog("OK " + combinedResp.split("\n")[0], "success");
                reviewProgress.setTitle("Review complete");
                reviewProgress.setState("success");
                reviewProgress.hide();
                chatHistory.push({role: "assistant", text: combinedResp});
                requestHint.textContent = "Review complete";
                requestHint.style.color = "#2e7d32";
                return;
            }

            var res = await fetch("/api/confluence-review-stream", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(withUserAuthPayload({
                    page_id: pageId,
                    page_input: link,
                    doc_type: docType,
                    checklist: checklist,
                    outputs: outputs,
                    prompt: msg,
                    history: chatHistory,
                    confluence_base_url: confluenceBaseUrl
                }))
            });

            if (!res.ok || !res.body) {
                reviewProgress.addLog("Streaming unavailable, using direct review...");
                var res2 = await fetch("/api/chat", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(withUserAuthPayload({prompt: msg, history: chatHistory}))
                });
                var data2 = await res2.json();
                var resp = data2.response || data2.error || "No response";
                var fallbackFailed = !res2.ok || !!data2.error || isErrorLikeText(resp);
                if (fallbackFailed) {
                    var fallbackErr = toActionableErrorMessage(extractErrorMessage(data2, resp));
                    markConnectionStatusFromError(fallbackErr);
                    reviewProgress.addLog("Error: " + fallbackErr, "error");
                    reviewProgress.setTitle("Review failed");
                    reviewProgress.setState("error");
                    requestHint.textContent = "Review failed";
                    requestHint.style.color = "#d32f2f";
                    chatHistory.push({role: "assistant", text: fallbackErr});
                    createAssistantMessageBubble(fallbackErr);
                    return;
                }
                reviewProgress.addLog("OK " + resp.split("\n")[0], "success");
                reviewProgress.setTitle("Review complete");
                reviewProgress.setState("success");
                reviewProgress.hide();
                chatHistory.push({role: "assistant", text: resp});
                requestHint.textContent = "Review complete";
                requestHint.style.color = "#2e7d32";
                return;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let assistantText = "";
            let buffer = "";
            let seenFirstChunk = false;
            reviewProgress.addLog("Review started...");

            while (true) {
                const result = await reader.read();
                if (result.done) {
                    break;
                }
                buffer += decoder.decode(result.value, {stream: true});
                const chunks = buffer.split("\n\n");
                buffer = chunks.pop();
                for (const chunk of chunks) {
                    if (!chunk.trim()) continue;
                    let eventType = null;
                    let dataLines = [];
                    chunk.split("\n").forEach(function(line) {
                        if (line.startsWith("event:")) {
                            eventType = line.slice(6).trim();
                        } else if (line.startsWith("data:")) {
                            dataLines.push(line.slice(5).trim());
                        }
                    });
                    if (!dataLines.length) continue;
                    const payloadText = dataLines.join("\n");
                    let payload;
                    try {
                        payload = JSON.parse(payloadText);
                    } catch (parseErr) {
                        payload = {message: payloadText};
                    }
                    var normalizedEventType = eventType || payload.type || "message";
                    if (normalizedEventType === "progress") {
                        normalizedEventType = "message";
                    }
                    if (normalizedEventType === "message" && payload.message) {
                        reviewProgress.addLog(payload.message, payload.level || "info");
                        if (!seenFirstChunk) {
                            seenFirstChunk = true;
                            reviewProgress.setState("active");
                        }
                    } else if (normalizedEventType === "token" && payload.token) {
                        assistantText += payload.token;
                        if (!seenFirstChunk) {
                            seenFirstChunk = true;
                            reviewProgress.setState("active");
                        }
                    } else if (normalizedEventType === "done") {
                        if (payload.response) {
                            assistantText = payload.response;
                        }
                        if (payload.summary) {
                            reviewProgress.addLog(payload.summary, "success");
                        }
                        if (payload.message) {
                            reviewProgress.addLog(payload.message, payload.level || "success");
                        }
                    } else if (eventType === "error") {
                        throw new Error(payload.error || payload.message || "Review failed");
                    }
                }
            }

            if (buffer.trim()) {
                try {
                    const tailPayload = JSON.parse(buffer.replace(/^data:\s*/, ""));
                    if (tailPayload.summary) {
                        reviewProgress.addLog(tailPayload.summary, "success");
                    }
                    if (tailPayload.response) {
                        assistantText = tailPayload.response;
                    }
                } catch (tailErr) {
                    /* ignore trailing parse issues */
                }
            }

            if (!assistantText.trim()) {
                assistantText = "Review failed: No response returned from review stream.";
            }

            const botBubble = createStreamingBubble();
            streamText(botBubble.textEl, botBubble.cursorEl, assistantText, 8);
            chatHistory.push({role: "assistant", text: assistantText});
            if (isErrorLikeText(assistantText)) {
                reviewProgress.setTitle("Review failed");
                reviewProgress.setState("error");
                requestHint.textContent = "Review failed";
                requestHint.style.color = "#d32f2f";
            } else {
                reviewProgress.setTitle("Review complete");
                reviewProgress.setState("success");
                requestHint.textContent = "Review complete";
                requestHint.style.color = "#2e7d32";
                reviewProgress.hide();
            }
        } catch (err) {
            var streamErr = toActionableErrorMessage(err.message);
            markConnectionStatusFromError(streamErr);
            reviewProgress.addLog("Error: " + streamErr, "error");
            reviewProgress.setTitle("Review failed");
            reviewProgress.setState("error");
            requestHint.textContent = "Error: " + streamErr;
            requestHint.style.color = "#d32f2f";
        }
    })();
});

// --- Help Modal Handlers ---
    const helpModal = document.getElementById('help-modal');
    const helpBtn = document.getElementById('help-guide-btn');
    const helpCloseBtn = document.getElementById('help-modal-close');
    const helpSidebarBtn = document.getElementById('help-guide-sidebar-btn');
    const startTourBtn = document.getElementById('start-tour-btn');

    function openHelpModal() {
        if (helpModal) {
            helpModal.classList.add('active');
        }
    }

    function closeHelpModal() {
        if (helpModal) {
            helpModal.classList.remove('active');
        }
    }

    if (helpBtn && helpModal) {
        helpBtn.addEventListener('click', openHelpModal);
    }
    if (helpSidebarBtn && helpModal) {
        helpSidebarBtn.addEventListener('click', openHelpModal);
    }

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
    const authSettingsCloseBtn = document.getElementById('auth-settings-close');
    const authSettingsView = document.getElementById('auth-settings-view');
    const clearAuthBtn = document.getElementById('clear-auth-btn');
    const editConfluenceAuthBtn = document.getElementById('edit-confluence-auth-btn');
    const editGithubAuthBtn = document.getElementById('edit-github-auth-btn');

    const confluenceAuthModal = document.getElementById('confluence-auth-modal');
    const confluenceAuthCloseBtn = document.getElementById('confluence-auth-close');
    const saveConfluenceAuthBtn = document.getElementById('save-confluence-auth-btn');
    const cancelConfluenceAuthBtn = document.getElementById('cancel-confluence-auth-btn');

    const githubAuthModal = document.getElementById('github-auth-modal');
    const githubAuthCloseBtn = document.getElementById('github-auth-close');
    const saveGithubAuthBtn = document.getElementById('save-github-auth-btn');
    const cancelGithubAuthBtn = document.getElementById('cancel-github-auth-btn');

    const editConfluenceEmail = document.getElementById('edit-confluence-email');
    const editConfluenceToken = document.getElementById('edit-confluence-token');
    const editGithubOwner = document.getElementById('edit-github-owner');
    const editGithubToken = document.getElementById('edit-github-token');

    const viewConfluenceEmail = document.getElementById('view-confluence-email');
    const viewConfluenceToken = document.getElementById('view-confluence-token');
    const viewGithubOwner = document.getElementById('view-github-owner');
    const viewGithubToken = document.getElementById('view-github-token');
    const viewConfluenceBaseUrl = document.getElementById('view-confluence-base-url');
    const viewGithubBaseUrl = document.getElementById('view-github-base-url');
    const viewConfluenceConnectionStatus = document.getElementById('view-confluence-connection-status');
    const viewGithubConnectionStatus = document.getElementById('view-github-connection-status');
    const githubConnectionPill = document.getElementById('github-connection-pill');
    const confluenceConnectionPill = document.getElementById('confluence-connection-pill');
    const authHistorySelect = document.getElementById('auth-history-select');
    const restoreAuthBtn = document.getElementById('restore-auth-btn');
    const deleteAuthRecordBtn = document.getElementById('delete-auth-record-btn');
    const testAuthBtn = document.getElementById('test-auth-btn');
    const authTestResult = document.getElementById('auth-test-result');

    const editConfluenceBaseUrl = document.getElementById('edit-confluence-base-url');
    const editGithubBaseUrl = document.getElementById('edit-github-base-url');

    function loadConnectionStatus() {
        try {
            const raw = localStorage.getItem(USER_CONNECTION_STATUS_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object') return null;
            return parsed;
        } catch (err) {
            return null;
        }
    }

    function saveConnectionStatus(status) {
        if (!status || typeof status !== 'object') {
            localStorage.removeItem(USER_CONNECTION_STATUS_KEY);
            return;
        }
        localStorage.setItem(USER_CONNECTION_STATUS_KEY, JSON.stringify(status));
    }

    function makeDefaultConnectionStatus(auth) {
        const hasGithub = !!(auth.github_owner && auth.github_token);
        const hasConfluence = !!(auth.confluence_email && auth.confluence_api_token && auth.confluence_base_url);
        return {
            github: {
                state: hasGithub ? 'untested' : 'missing',
                message: hasGithub ? 'Credentials set. Run Test Connection.' : 'Missing owner or token.'
            },
            confluence: {
                state: hasConfluence ? 'untested' : 'missing',
                message: hasConfluence ? 'Credentials set. Run Test Connection.' : 'Missing email, token, or base URL.'
            },
            tested_at: ''
        };
    }

    function setConnectionPill(pillEl, label, state) {
        if (!pillEl) return;
        var normalized = state || 'unknown';
        pillEl.classList.remove('valid', 'invalid', 'missing', 'untested', 'unknown');
        pillEl.classList.add(normalized);
        var pretty = normalized.charAt(0).toUpperCase() + normalized.slice(1);
        pillEl.textContent = label + ': ' + pretty;
    }

    function renderConnectionStatus() {
        const auth = loadUserAuth();
        const stored = loadConnectionStatus() || makeDefaultConnectionStatus(auth);
        const githubState = (stored.github && stored.github.state) || 'unknown';
        const githubMsg = (stored.github && stored.github.message) || 'Unknown';
        const confState = (stored.confluence && stored.confluence.state) || 'unknown';
        const confMsg = (stored.confluence && stored.confluence.message) || 'Unknown';

        setConnectionPill(githubConnectionPill, 'GitHub', githubState);
        setConnectionPill(confluenceConnectionPill, 'Confluence', confState);

        if (viewGithubConnectionStatus) viewGithubConnectionStatus.textContent = githubState.toUpperCase() + ' - ' + githubMsg;
        if (viewConfluenceConnectionStatus) viewConfluenceConnectionStatus.textContent = confState.toUpperCase() + ' - ' + confMsg;
    }

    function resetConnectionStatusFromAuth(auth) {
        const clean = normalizeUserAuth(auth || {});
        saveConnectionStatus(makeDefaultConnectionStatus(clean));
        renderConnectionStatus();
    }

    function markConnectionStatusFromError(message) {
        var text = String(message || '').toLowerCase();
        if (!text) return;
        var status = loadConnectionStatus() || makeDefaultConnectionStatus(loadUserAuth());
        var touched = false;

        if (text.includes('github') || text.includes('/repos/') || text.includes('pull request')) {
            status.github = { state: 'invalid', message: 'Token/session may be expired or invalid. Re-test credentials.' };
            touched = true;
        }
        if (text.includes('confluence') || text.includes('/rest/api/') || text.includes('page id')) {
            status.confluence = { state: 'invalid', message: 'Token/session may be expired or invalid. Re-test credentials.' };
            touched = true;
        }

        if (!touched && isAuthErrorLikeText(text)) {
            if (status.github && status.github.state !== 'missing') {
                status.github = { state: 'invalid', message: 'Token/session may be expired or invalid. Re-test credentials.' };
            }
            if (status.confluence && status.confluence.state !== 'missing') {
                status.confluence = { state: 'invalid', message: 'Token/session may be expired or invalid. Re-test credentials.' };
            }
        }

        saveConnectionStatus(status);
        renderConnectionStatus();
    }

    async function runConnectionTest(authCandidate) {
        if (authTestResult) {
            authTestResult.textContent = 'Testing connections...';
            authTestResult.style.color = '#555';
        }
        try {
            const res = await fetch('/api/test-connections', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_auth: normalizeUserAuth(authCandidate || {}) })
            });
            const data = await res.json();
            if (!res.ok || (data && data.success === false)) {
                throw new Error((data && (data.error || data.message)) || 'Connection test request failed.');
            }
            const results = (data && data.results) || {};
            const status = {
                github: results.github || { state: 'unknown', message: 'No result' },
                confluence: results.confluence || { state: 'unknown', message: 'No result' },
                tested_at: new Date().toISOString()
            };
            const normalizedCandidate = normalizeUserAuth(authCandidate || {});
            const hasUsableGithub = status.github && status.github.state === 'valid';
            const hasUsableConfluence = status.confluence && status.confluence.state === 'valid';
            if (hasUsableGithub || hasUsableConfluence) {
                saveUserAuth(normalizedCandidate);
                renderAuthView();
                renderAuthHistoryOptions();
            }
            saveConnectionStatus(status);
            renderConnectionStatus();
            if (authTestResult) {
                authTestResult.textContent = 'GitHub: ' + (status.github.state || 'unknown') + ' | Confluence: ' + (status.confluence.state || 'unknown');
                authTestResult.style.color = (status.github.state === 'valid' && status.confluence.state === 'valid') ? '#1b7f3b' : '#b42318';
            }
        } catch (err) {
            const msg = toActionableErrorMessage(err && err.message ? err.message : 'Connection test failed.');
            markConnectionStatusFromError(msg);
            if (authTestResult) {
                authTestResult.textContent = 'Connection test failed: ' + msg;
                authTestResult.style.color = '#b42318';
            }
        }
    }

    function setSavedTokenValue(viewEl, value) {
        if (!viewEl) return;
        var token = String(value || '');
        viewEl.dataset.secret = token;
        viewEl.dataset.revealed = 'false';
        viewEl.textContent = token ? maskSecret(token) : 'Not set';
    }

    function resetTokenToggleButtons() {
        document.querySelectorAll('.token-toggle').forEach(function(btn) {
            const targetId = btn.getAttribute('data-target');
            const targetInput = targetId ? document.getElementById(targetId) : null;
            if (targetInput && targetInput.tagName === 'INPUT') {
                targetInput.type = 'password';
                btn.textContent = 'Show';
                btn.setAttribute('aria-pressed', 'false');
                return;
            }
            if (targetInput) {
                targetInput.dataset.revealed = 'false';
                var secret = String(targetInput.dataset.secret || '');
                targetInput.textContent = secret ? maskSecret(secret) : 'Not set';
            }
            btn.textContent = 'Show';
            btn.setAttribute('aria-pressed', 'false');
        });
    }

    if (testAuthBtn) {
        testAuthBtn.addEventListener('click', function() {
            runConnectionTest(loadUserAuth());
        });
    }

    if (restoreAuthBtn) {
        restoreAuthBtn.addEventListener('click', function() {
            const selected = getSelectedAuthHistoryRecord();
            if (!selected || !selected.auth) {
                showUiAlert('Please select a credential record to restore.');
                return;
            }
            saveUserAuth(selected.auth);
            resetConnectionStatusFromAuth(selected.auth);
            renderAuthView();
            fillConfluenceEditFields();
            fillGithubEditFields();
        });
    }

    if (deleteAuthRecordBtn) {
        deleteAuthRecordBtn.addEventListener('click', function() {
            const selected = getSelectedAuthHistoryRecord();
            if (!selected) {
                showUiAlert('Please select a credential record to delete.');
                return;
            }
            const history = loadAuthHistory().filter(function(entry) {
                return entry.id !== selected.id;
            });
            saveAuthHistory(history);
            renderAuthHistoryOptions();
        });
    }

    function fillConfluenceEditFields() {
        const auth = loadUserAuth();
        if (editConfluenceEmail) editConfluenceEmail.value = auth.confluence_email || '';
        if (editConfluenceToken) editConfluenceToken.value = auth.confluence_api_token || '';
        if (editConfluenceBaseUrl) editConfluenceBaseUrl.value = auth.confluence_base_url || '';
        resetTokenToggleButtons();
    }

    function fillGithubEditFields() {
        const auth = loadUserAuth();
        if (editGithubOwner) editGithubOwner.value = auth.github_owner || '';
        if (editGithubToken) editGithubToken.value = auth.github_token || '';
        if (editGithubBaseUrl) editGithubBaseUrl.value = auth.github_base_url || 'https://api.github.com';
        resetTokenToggleButtons();
    }

    function renderAuthView() {
        const auth = loadUserAuth();
        if (viewConfluenceEmail) viewConfluenceEmail.textContent = auth.confluence_email || 'Not set';
        setSavedTokenValue(viewConfluenceToken, auth.confluence_api_token || '');
        if (viewGithubOwner) viewGithubOwner.textContent = auth.github_owner || 'Not set';
        setSavedTokenValue(viewGithubToken, auth.github_token || '');
        if (viewConfluenceBaseUrl) viewConfluenceBaseUrl.textContent = auth.confluence_base_url || 'Not set';
        if (viewGithubBaseUrl) viewGithubBaseUrl.textContent = auth.github_base_url || 'https://api.github.com';
        resetTokenToggleButtons();
        renderConnectionStatus();
    }

    function renderAuthHistoryOptions() {
        if (!authHistorySelect) return;
        const history = loadAuthHistory();
        authHistorySelect.innerHTML = '';

        if (!history.length) {
            const none = document.createElement('option');
            none.value = '';
            none.textContent = 'No saved credential records yet';
            authHistorySelect.appendChild(none);
            authHistorySelect.disabled = true;
            if (restoreAuthBtn) restoreAuthBtn.disabled = true;
            if (deleteAuthRecordBtn) deleteAuthRecordBtn.disabled = true;
            return;
        }

        authHistorySelect.disabled = false;
        if (restoreAuthBtn) restoreAuthBtn.disabled = false;
        if (deleteAuthRecordBtn) deleteAuthRecordBtn.disabled = false;

        history.forEach(function(entry) {
            const option = document.createElement('option');
            option.value = entry.id;
            option.textContent = formatAuthHistoryLabel(entry);
            authHistorySelect.appendChild(option);
        });
    }

    function getSelectedAuthHistoryRecord() {
        if (!authHistorySelect || !authHistorySelect.value) return null;
        const selectedId = authHistorySelect.value;
        const history = loadAuthHistory();
        for (var i = 0; i < history.length; i += 1) {
            if (history[i].id === selectedId) {
                return history[i];
            }
        }
        return null;
    }

    function openAuthModal() {
        renderAuthView();
        renderAuthHistoryOptions();
        fillConfluenceEditFields();
        fillGithubEditFields();
        if (authTestResult) {
            authTestResult.textContent = 'Run a connection test to validate credentials.';
            authTestResult.style.color = '#666';
        }
        if (authSettingsModal) authSettingsModal.classList.add('active');
    }

    function closeAuthModal() {
        if (authSettingsModal) authSettingsModal.classList.remove('active');
        resetTokenToggleButtons();
    }

    function openConfluenceAuthModal() {
        fillConfluenceEditFields();
        if (confluenceAuthModal) confluenceAuthModal.classList.add('active');
    }

    function closeConfluenceAuthModal() {
        if (confluenceAuthModal) confluenceAuthModal.classList.remove('active');
        resetTokenToggleButtons();
    }

    function openGithubAuthModal() {
        fillGithubEditFields();
        if (githubAuthModal) githubAuthModal.classList.add('active');
    }

    function closeGithubAuthModal() {
        if (githubAuthModal) githubAuthModal.classList.remove('active');
        resetTokenToggleButtons();
    }

    function saveConfluenceCredentials() {
        const current = loadUserAuth();
        const nextAuth = Object.assign({}, current, {
            confluence_email: editConfluenceEmail ? editConfluenceEmail.value : '',
            confluence_api_token: editConfluenceToken ? editConfluenceToken.value : '',
            confluence_base_url: editConfluenceBaseUrl ? editConfluenceBaseUrl.value : ''
        });
        const saved = saveUserAuth(nextAuth);
        resetConnectionStatusFromAuth(saved);
        addAuthHistoryRecord(saved);
        renderAuthView();
        renderAuthHistoryOptions();
        closeConfluenceAuthModal();
    }

    function saveGithubCredentials() {
        const current = loadUserAuth();
        const nextAuth = Object.assign({}, current, {
            github_owner: editGithubOwner ? editGithubOwner.value : '',
            github_token: editGithubToken ? editGithubToken.value : '',
            github_base_url: editGithubBaseUrl ? editGithubBaseUrl.value : 'https://api.github.com'
        });
        const saved = saveUserAuth(nextAuth);
        resetConnectionStatusFromAuth(saved);
        addAuthHistoryRecord(saved);
        renderAuthView();
        renderAuthHistoryOptions();
        closeGithubAuthModal();
    }

    if (authSettingsBtn && authSettingsModal) {
        authSettingsBtn.addEventListener('click', openAuthModal);
    }

    if (authSettingsCloseBtn && authSettingsModal) {
        authSettingsCloseBtn.addEventListener('click', closeAuthModal);
        authSettingsModal.addEventListener('click', function(event) {
            if (event.target === authSettingsModal) {
                closeAuthModal();
            }
        });
    }

    if (editConfluenceAuthBtn) {
        editConfluenceAuthBtn.addEventListener('click', openConfluenceAuthModal);
    }

    if (editGithubAuthBtn) {
        editGithubAuthBtn.addEventListener('click', openGithubAuthModal);
    }

    if (confluenceAuthCloseBtn && confluenceAuthModal) {
        confluenceAuthCloseBtn.addEventListener('click', closeConfluenceAuthModal);
        confluenceAuthModal.addEventListener('click', function(event) {
            if (event.target === confluenceAuthModal) {
                closeConfluenceAuthModal();
            }
        });
    }

    if (githubAuthCloseBtn && githubAuthModal) {
        githubAuthCloseBtn.addEventListener('click', closeGithubAuthModal);
        githubAuthModal.addEventListener('click', function(event) {
            if (event.target === githubAuthModal) {
                closeGithubAuthModal();
            }
        });
    }

    if (cancelConfluenceAuthBtn) {
        cancelConfluenceAuthBtn.addEventListener('click', closeConfluenceAuthModal);
    }

    if (cancelGithubAuthBtn) {
        cancelGithubAuthBtn.addEventListener('click', closeGithubAuthModal);
    }

    if (saveConfluenceAuthBtn) {
        saveConfluenceAuthBtn.addEventListener('click', saveConfluenceCredentials);
    }

    if (saveGithubAuthBtn) {
        saveGithubAuthBtn.addEventListener('click', saveGithubCredentials);
    }

    if (clearAuthBtn) {
        clearAuthBtn.addEventListener('click', function() {
            saveUserAuth({});
            resetConnectionStatusFromAuth({});
            renderAuthView();
            renderAuthHistoryOptions();
            fillConfluenceEditFields();
            fillGithubEditFields();
        });
    }

    document.querySelectorAll('.token-toggle').forEach(function(btn) {
        btn.addEventListener('click', function() {
            const targetId = btn.getAttribute('data-target');
            const targetInput = targetId ? document.getElementById(targetId) : null;
            if (!targetInput) return;

            if (targetInput.tagName === 'INPUT') {
                const isHidden = targetInput.type === 'password';
                targetInput.type = isHidden ? 'text' : 'password';
                btn.textContent = isHidden ? 'Hide' : 'Show';
                btn.setAttribute('aria-pressed', isHidden ? 'true' : 'false');
                return;
            }

            const wasRevealed = targetInput.dataset.revealed === 'true';
            const secret = String(targetInput.dataset.secret || '');
            const revealNext = !wasRevealed;
            targetInput.dataset.revealed = revealNext ? 'true' : 'false';
            targetInput.textContent = secret ? (revealNext ? secret : maskSecret(secret)) : 'Not set';
            btn.textContent = revealNext ? 'Hide' : 'Show';
            btn.setAttribute('aria-pressed', revealNext ? 'true' : 'false');
        });
    });

    function openPrReviewPanel() {
        if (!prReviewPanel) return;
        prReviewPanel.style.display = 'flex';
        mainChat.style.display = 'none';
        sidebar.style.display = 'none';
    }

    function closePrReviewPanel() {
        if (!prReviewPanel) return;
        prReviewPanel.style.display = 'none';
        mainChat.style.display = 'flex';
        sidebar.style.display = '';
    }

    function openConfluencePanel() {
        if (!confPanel) return;
        confPanel.style.display = 'flex';
        mainChat.style.display = 'none';
        sidebar.style.display = 'none';
    }

    function closeConfluencePanel() {
        if (!confPanel) return;
        confPanel.style.display = 'none';
        mainChat.style.display = 'flex';
        sidebar.style.display = '';
    }

    function resetToMainChatView() {
        closePrReviewPanel();
        closeConfluencePanel();
        closeAuthModal();
        closeHelpModal();
    }

    const onboarding = {
        active: false,
        stepIndex: 0,
        overlayEl: null,
        cardEl: null,
        targetEl: null,
        steps: [
            {
                selector: '#help-modal .help-modal-content',
                placement: 'bottom',
                title: 'Quick Guide Overview',
                text: 'This guide introduces the major features. You can reopen it anytime from the sidebar footer.',
                before: function() {
                    resetToMainChatView();
                    openHelpModal();
                }
            },
            {
                selector: '#auth-settings-btn',
                placement: 'right',
                title: 'Account Setup',
                text: 'Set your GitHub owner/token and Confluence email/token here. These credentials power runtime review requests.',
                before: function() {
                    resetToMainChatView();
                }
            },
            {
                selector: '#pr-review-btn',
                placement: 'right',
                title: 'GitHub PR Review',
                text: 'Open the PR review panel to parse links, choose checklist rules, and generate inline + summary feedback.',
                before: function() {
                    resetToMainChatView();
                }
            },
            {
                selector: '#pr-link-input',
                placement: 'bottom',
                title: 'PR Link Parser',
                text: 'Paste a GitHub pull request URL and click Parse to validate owner/repo/PR values before review starts.',
                before: function() {
                    closeHelpModal();
                    closeConfluencePanel();
                    openPrReviewPanel();
                }
            },
            {
                selector: '#start-review-btn',
                placement: 'top',
                title: 'Start PR Review',
                text: 'Run the configured checklist and expected outputs. Progress appears live in chat.',
                before: function() {
                    openPrReviewPanel();
                }
            },
            {
                selector: '#quick-review-btn',
                placement: 'right',
                title: 'Quick Review',
                text: 'Choose between combined, Confluence-only, or PR-only review modes.',
                before: function() {
                    closePrReviewPanel();
                    closeHelpModal();
                }
            },
            {
                selector: '#confluence-link-input',
                placement: 'bottom',
                title: 'Confluence Link / Page ID',
                text: 'Use a page URL or numeric page ID, parse it, then launch a checklist-based page review.',
                before: function() {
                    closePrReviewPanel();
                    openConfluencePanel();
                }
            },
            {
                selector: '#input',
                placement: 'top',
                title: 'Chat Prompt Area',
                text: 'Use chat for custom instructions, follow-up checks, and flexible review requests beyond the quick panels.',
                before: function() {
                    closeConfluencePanel();
                    closeHelpModal();
                }
            },
            {
                selector: '#request-hint',
                placement: 'top',
                title: 'Live Status Hint',
                text: 'This status text updates with current progress and completion state while reviews run.',
                before: function() {
                    closeConfluencePanel();
                    closePrReviewPanel();
                }
            }
        ]
    };

    function clearTourTarget() {
        if (onboarding.targetEl) {
            onboarding.targetEl.classList.remove('tour-target-highlight');
            onboarding.targetEl = null;
        }
    }

    function destroyTourElements() {
        if (onboarding.cardEl && onboarding.cardEl.parentNode) {
            onboarding.cardEl.parentNode.removeChild(onboarding.cardEl);
        }
        if (onboarding.overlayEl && onboarding.overlayEl.parentNode) {
            onboarding.overlayEl.parentNode.removeChild(onboarding.overlayEl);
        }
        onboarding.cardEl = null;
        onboarding.overlayEl = null;
    }

    function endOnboardingTour() {
        onboarding.active = false;
        clearTourTarget();
        destroyTourElements();
        resetToMainChatView();
    }

    function getStepTarget(step) {
        if (!step || !step.selector) return null;
        return document.querySelector(step.selector);
    }

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function positionTourCard(card, target, placement) {
        const rect = target.getBoundingClientRect();
        const gap = 14;
        const cardRect = card.getBoundingClientRect();
        const maxLeft = window.innerWidth - cardRect.width - 10;
        const maxTop = window.innerHeight - cardRect.height - 10;

        let left = rect.left;
        let top = rect.top;

        if (placement === 'left') {
            left = rect.left - cardRect.width - gap;
            top = rect.top;
        } else if (placement === 'right') {
            left = rect.right + gap;
            top = rect.top;
        } else if (placement === 'top') {
            left = rect.left;
            top = rect.top - cardRect.height - gap;
        } else {
            left = rect.left;
            top = rect.bottom + gap;
        }

        card.style.left = clamp(left, 8, maxLeft) + 'px';
        card.style.top = clamp(top, 8, maxTop) + 'px';
        card.dataset.placement = placement || 'bottom';
    }

    function renderTourStep() {
        if (!onboarding.active) return;

        const step = onboarding.steps[onboarding.stepIndex];
        if (!step) {
            endOnboardingTour();
            return;
        }

        if (typeof step.before === 'function') {
            step.before();
        }

        const target = getStepTarget(step);
        if (!target) {
            onboarding.stepIndex += 1;
            renderTourStep();
            return;
        }

        target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });

        window.setTimeout(function() {
            if (!onboarding.active) return;
            clearTourTarget();
            onboarding.targetEl = target;
            onboarding.targetEl.classList.add('tour-target-highlight');

            if (!onboarding.overlayEl) {
                onboarding.overlayEl = document.createElement('div');
                onboarding.overlayEl.className = 'tour-overlay';
                onboarding.overlayEl.addEventListener('click', function() {
                    endOnboardingTour();
                });
                document.body.appendChild(onboarding.overlayEl);
            }

            if (!onboarding.cardEl) {
                onboarding.cardEl = document.createElement('div');
                onboarding.cardEl.className = 'tour-card';
                document.body.appendChild(onboarding.cardEl);
            }

            const isLast = onboarding.stepIndex === onboarding.steps.length - 1;
            onboarding.cardEl.innerHTML =
                '<span class="tour-step-label">Step ' + (onboarding.stepIndex + 1) + ' of ' + onboarding.steps.length + '</span>' +
                '<h4 class="tour-title">' + step.title + '</h4>' +
                '<p class="tour-text">' + step.text + '</p>' +
                '<div class="tour-controls">' +
                    '<button type="button" class="tour-btn" data-action="skip">Skip</button>' +
                    '<div class="tour-controls-right">' +
                        '<button type="button" class="tour-btn" data-action="prev" ' + (onboarding.stepIndex === 0 ? 'disabled' : '') + '>Back</button>' +
                        '<button type="button" class="tour-btn tour-btn-primary" data-action="next">' + (isLast ? 'Finish' : 'Next') + '</button>' +
                    '</div>' +
                '</div>';

            positionTourCard(onboarding.cardEl, target, step.placement || 'bottom');

            onboarding.cardEl.querySelector('[data-action="skip"]').addEventListener('click', function() {
                endOnboardingTour();
            });

            const prevBtn = onboarding.cardEl.querySelector('[data-action="prev"]');
            if (prevBtn) {
                prevBtn.addEventListener('click', function() {
                    if (onboarding.stepIndex > 0) {
                        onboarding.stepIndex -= 1;
                        renderTourStep();
                    }
                });
            }

            onboarding.cardEl.querySelector('[data-action="next"]').addEventListener('click', function() {
                if (isLast) {
                    endOnboardingTour();
                    return;
                }
                onboarding.stepIndex += 1;
                renderTourStep();
            });
        }, 120);
    }

    function markOnboardingSeen() {
        localStorage.setItem(ONBOARDING_SEEN_KEY, '1');
    }

    function startOnboardingTour() {
        if (onboarding.active) return;
        onboarding.active = true;
        onboarding.stepIndex = 0;
        markOnboardingSeen();
        renderTourStep();
    }

    if (startTourBtn) {
        startTourBtn.addEventListener('click', function() {
            closeHelpModal();
            startOnboardingTour();
        });
    }

    renderAuthView();
    resetConnectionStatusFromAuth(loadUserAuth());

    if (!localStorage.getItem(ONBOARDING_SEEN_KEY)) {
        window.setTimeout(function() {
            openHelpModal();
            startOnboardingTour();
        }, 500);
    }
