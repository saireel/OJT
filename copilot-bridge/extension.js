const vscode = require("vscode");
const http = require("http");

let server = null;
let selectedModelName = null;
let availableModels = [];
const PORT = 5100;

async function handlePrompt(promptText) {
    const models = await vscode.lm.selectChatModels({
        vendor: "copilot",
        family: "gpt-4o"
    });

    if (!models || models.length === 0) {
        throw new Error(
            "No Copilot model available. Make sure GitHub Copilot Chat is installed and you are signed in."
        );
    }

    const model = models[0];
    const messages = [vscode.LanguageModelChatMessage.User(promptText)];
    const response = await model.sendRequest(messages, {}, new vscode.CancellationTokenSource().token);

    let result = "";
    for await (const chunk of response.text) {
        result += chunk;
    }
    return result;
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
            res.end(JSON.stringify({ status: "Copilot Bridge is running" }));
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
                    res.end(JSON.stringify({ response: answer }));
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
