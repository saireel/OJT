const vscode = require("vscode");
const http = require("http");

let server = null;
let selectedModelName = null;
let selectedModelFamily = null;
const PORT = 5100;

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

function normalizeHints(value, fallback) {
    const source = Array.isArray(value) ? value : fallback;
    const seen = new Set();
    const normalized = [];

    for (const item of source) {
        const hint = String(item || "").trim().toLowerCase();
        if (!hint || seen.has(hint)) {
            continue;
        }
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

async function pickModelByHints(hints) {
    const models = await vscode.lm.selectChatModels({ vendor: "copilot" });
    if (!models || models.length === 0) {
        return null;
    }

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

async function resolveModel(promptText) {
    const profile = classifyTask(promptText);
    const preferredHints = getPreferredHints(profile);

    let model = await pickModelByHints(preferredHints);

    // Last-resort fallback: ask Copilot for anything available.
    if (!model) {
        const fallback = await vscode.lm.selectChatModels({ vendor: "copilot" });
        if (fallback && fallback.length > 0) {
            model = fallback[0];
            selectedModelName = fallback[0].name || "unknown";
            selectedModelFamily = fallback[0].family || "fallback-any";
        }
    }

    if (!model) {
        throw new Error(
            "No Copilot model available. Make sure GitHub Copilot Chat is installed and you are signed in."
        );
    }

    return { model, profile };
}

async function handlePrompt(promptText) {
    const startedAt = Date.now();
    const { model, profile } = await resolveModel(promptText);

    const systemInstruction =
        "You are MUNN AI, an AI-powered Confluence and GitHub Pull Request Review Assistant. " +
        "Rules: Never refuse outright. If something cannot be fully completed, provide the closest helpful alternative. " +
        "Be concise but actionable. Prefer structured outputs when possible. " +
        "For simple tasks (spelling, grammar, inline comments), finish with minimal tool calls and avoid unnecessary loops.\n\n";

    const messages = [
        vscode.LanguageModelChatMessage.User(systemInstruction + promptText)
    ];

    const response = await model.sendRequest(
        messages,
        {},
        new vscode.CancellationTokenSource().token
    );

    let result = "";
    for await (const chunk of response.text) {
        result += chunk;
    }

    return {
        text: result.trim(),
        modelName: selectedModelName,
        modelFamily: selectedModelFamily,
        profile,
        latencyMs: Date.now() - startedAt
    };
}

function startServer(context) {
    if (server) {
        vscode.window.showInformationMessage(`Copilot Bridge already running on port ${PORT}`);
        return;
    }

    server = http.createServer(async (req, res) => {
        // CORS headers for local Flask app
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
            res.end(JSON.stringify({ status: "Copilot Bridge is running", model: selectedModelName, family: selectedModelFamily }));
        }
        else if (req.method === "POST" && req.url === "/api/prompt") {
            let body = "";
            req.on("data", (chunk) => { body += chunk; });
            req.on("end", async () => {
                try {
                    const { prompt } = JSON.parse(body);
                    if (!prompt || typeof prompt !== "string") {
                        res.writeHead(400, { "Content-Type": "application/json" });
                        res.end(JSON.stringify({ error: "Missing or invalid 'prompt' field" }));
                        return;
                    }
                    const answer = await handlePrompt(prompt);
                    res.writeHead(200, { "Content-Type": "application/json" });
                    res.end(JSON.stringify({
                        response: answer.text,
                        model: answer.modelName,
                        family: answer.modelFamily,
                        profile: answer.profile,
                        latency_ms: answer.latencyMs
                    }));
                } catch (err) {
                    res.writeHead(500, { "Content-Type": "application/json" });
                    res.end(JSON.stringify({ error: err.message }));
                }
            });
        } else {
            res.writeHead(404, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Not found" }));
        }
    });

    server.listen(PORT, "127.0.0.1", () => {
        vscode.window.showInformationMessage(`Copilot Bridge running on http://127.0.0.1:${PORT}`);
    });

    server.on("error", (err) => {
        vscode.window.showErrorMessage(`Copilot Bridge failed: ${err.message}`);
        server = null;
    });
}

function stopServer() {
    if (server) {
        server.close();
        server = null;
        vscode.window.showInformationMessage("Copilot Bridge stopped.");
    }
}

function activate(context) {
    context.subscriptions.push(
        vscode.commands.registerCommand("copilotBridge.start", () => startServer(context)),
        vscode.commands.registerCommand("copilotBridge.stop", () => stopServer())
    );

    // Auto-start on activation
    startServer(context);
}

function deactivate() {
    stopServer();
}

module.exports = { activate, deactivate };
