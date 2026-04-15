const vscode = require("vscode");
const http = require("http");

let server = null;
let selectedModelName = null;
let selectedModelFamily = null;
const PORT = 5100;

// 🧠 Adaptive state
let preferFreeModels = false;
const modelStats = new Map(); // { modelName: { success, avgLatency } }

// -------------------- CLASSIFICATION --------------------
function classifyTask(promptText) {
    const text = String(promptText || "").toLowerCase();
    const fastKeywords = [
        "spell",
        "spelling",
        "grammar",
        "typo",
        "inline comment",
        "comment on",
        "find and replace"
    ];
    return fastKeywords.some((kw) => text.includes(kw)) ? "fast" : "default";
}

// -------------------- HINTS --------------------
function normalizeHints(value, fallback) {
    const source = Array.isArray(value) ? value : fallback;
    const seen = new Set();
    const normalized = [];

    for (const item of source) {
        const hint = String(item || "").trim().toLowerCase();
        if (!hint || seen.has(hint)) continue;
        seen.add(hint);
        normalized.push(hint);
    }

    return normalized.length > 0 ? normalized : fallback;
}

function getPreferredHints(profile) {
    const defaultFastHints = ["claude", "haiku", "sonnet"];
    const defaultRegularHints = ["claude", "sonnet", "opus"];
    const config = vscode.workspace.getConfiguration("copilotBridge");

    const fastHints = normalizeHints(
        config.get("fastModelHints", defaultFastHints),
        defaultFastHints
    );
    const regularHints = normalizeHints(
        config.get("defaultModelHints", defaultRegularHints),
        defaultRegularHints
    );

    return profile === "fast" ? fastHints : regularHints;
}

// -------------------- MODEL HELPERS --------------------
function isLikelyFreeModel(model) {
    const name = (model.name || "").toLowerCase();
    const family = (model.family || "").toLowerCase();

    return (
        name.includes("mini") ||
        name.includes("gpt-4o-mini") ||
        family.includes("mini") ||
        family.includes("basic")
    );
}

async function pickModelByHints(hints) {
    const models = await vscode.lm.selectChatModels({ vendor: "copilot" });
    if (!models || models.length === 0) return null;

    for (const hint of hints) {
        const normalizedHint = String(hint || "").toLowerCase();

        const match = models.find((model) => {
            const name = String(model.name || "").toLowerCase();
            const family = String(model.family || "").toLowerCase();
            return name.includes(normalizedHint) || family.includes(normalizedHint);
        });

        if (match) {
            selectedModelName = match.name || "unknown";
            selectedModelFamily = match.family || "unknown";
            return match;
        }
    }

    return null;
}

// -------------------- RESOLVE MODEL --------------------
async function resolveModel(promptText) {
    const profile = classifyTask(promptText);
    let hints = getPreferredHints(profile);

    //If quota was hit before → force free models
    if (preferFreeModels) {
        hints = ["mini", "gpt-4o-mini", "basic"];
    }

    let model = await pickModelByHints(hints);

    if (!model) {
        const fallback = await vscode.lm.selectChatModels({ vendor: "copilot" });
        if (fallback && fallback.length > 0) {
            model = fallback[0];
        }
    }

    if (!model) {
        throw new Error("No Copilot model available.");
    }

    return { model, profile };
}

// -------------------- SEND REQUEST --------------------
async function sendWithModel(model, messages, profile, startedAt) {
    const name = model.name || "unknown";

    const response = await model.sendRequest(
        messages,
        {},
        new vscode.CancellationTokenSource().token
    );

    let result = "";
    for await (const chunk of response.text) {
        result += chunk;
    }

    const latency = Date.now() - startedAt;

    // 📊 track performance
    const stat = modelStats.get(name) || { success: 0, avgLatency: latency };
    stat.success += 1;
    stat.avgLatency = (stat.avgLatency + latency) / 2;
    modelStats.set(name, stat);

    selectedModelName = name;
    selectedModelFamily = model.family || "unknown";

    return {
        text: result.trim(),
        modelName: name,
        modelFamily: selectedModelFamily,
        profile,
        latencyMs: latency
    };
}

// -------------------- HANDLE PROMPT --------------------
async function handlePrompt(promptText) {
    const startedAt = Date.now();
    const { model, profile } = await resolveModel(promptText);

    const systemInstruction =
        "You are MUNN AI, an AI-powered Confluence and GitHub Pull Request Review Assistant. " +
        "Rules: Never refuse outright. Provide the closest helpful answer. Be concise.\n\n";

    const messages = [
        vscode.LanguageModelChatMessage.User(systemInstruction + promptText)
    ];

    try {
        return await sendWithModel(model, messages, profile, startedAt);
    } catch (err) {
        console.warn("Primary model failed:", err.message);

        // 🔥 Detect quota → switch mode permanently
        if (err.message?.toLowerCase().includes("quota")) {
            preferFreeModels = true;
        }

        const allModels = await vscode.lm.selectChatModels({ vendor: "copilot" });

        let candidates = allModels;

        if (preferFreeModels) {
            candidates = allModels.filter(isLikelyFreeModel);
        }

        // 🧠 sort by learned latency
        candidates.sort((a, b) => {
            const statA = modelStats.get(a.name) || { avgLatency: Infinity };
            const statB = modelStats.get(b.name) || { avgLatency: Infinity };
            return statA.avgLatency - statB.avgLatency;
        });

        for (const fallback of candidates) {
            try {
                return await sendWithModel(fallback, messages, profile, startedAt);
            } catch {
                continue;
            }
        }

        throw new Error("All Copilot models failed.");
    }
}

// -------------------- SERVER --------------------
function startServer(context) {
    if (server) {
        vscode.window.showInformationMessage(`Copilot Bridge already running on port ${PORT}`);
        return;
    }

    server = http.createServer(async (req, res) => {
        res.setHeader("Access-Control-Allow-Origin", "http://127.0.0.1:5000");
        res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
        res.setHeader("Access-Control-Allow-Headers", "Content-Type");

        if (req.method === "OPTIONS") {
            res.writeHead(204);
            res.end();
            return;
        }

        if (req.method === "GET" && req.url === "/api/prompt") {
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({
                status: "running",
                model: selectedModelName,
                family: selectedModelFamily,
                preferFreeModels
            }));
        }

        else if (req.method === "POST" && req.url === "/api/prompt") {
            let body = "";

            req.on("data", (chunk) => { body += chunk; });

            req.on("end", async () => {
                try {
                    const { prompt } = JSON.parse(body);

                    if (!prompt) {
                        res.writeHead(400);
                        res.end(JSON.stringify({ error: "Missing prompt" }));
                        return;
                    }

                    const answer = await handlePrompt(prompt);

                    res.writeHead(200, { "Content-Type": "application/json" });
                    res.end(JSON.stringify({
                        response: answer.text,
                        model: answer.modelName,
                        family: answer.modelFamily,
                        profile: answer.profile,
                        latency_ms: answer.latencyMs,
                        preferFreeModels
                    }));

                } catch (err) {
                    res.writeHead(500);
                    res.end(JSON.stringify({ error: err.message }));
                }
            });
        }

        else {
            res.writeHead(404);
            res.end(JSON.stringify({ error: "Not found" }));
        }
    });

    server.listen(PORT, "127.0.0.1", () => {
        vscode.window.showInformationMessage(`Copilot Bridge running on http://127.0.0.1:${PORT}`);
    });
}

// -------------------- STOP --------------------
function stopServer() {
    if (server) {
        server.close();
        server = null;
        vscode.window.showInformationMessage("Copilot Bridge stopped.");
    }
}

// -------------------- ACTIVATE --------------------
function activate(context) {
    context.subscriptions.push(
        vscode.commands.registerCommand("copilotBridge.start", () => startServer(context)),
        vscode.commands.registerCommand("copilotBridge.stop", stopServer)
    );

    startServer(context);
}

function deactivate() {
    stopServer();
}

module.exports = { activate, deactivate };