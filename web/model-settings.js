"use strict";

let savedModelSettings = null;
let modelSettingsBusy = false;

function renderModelFields() {
  const saved = savedModelSettings;
  const provider = byId("model-provider").value;
  const compatible = provider === "compatible";
  const usesApi = provider !== "ollama";
  const connectedAddress = provider === saved?.provider
    && (!compatible || byId("model-url").value.trim().replace(/\/+$/, "") === saved.base_url);
  const reuseKey = Boolean(usesApi && connectedAddress && saved?.has_key);
  const disabled = !saved || saved.demo || modelSettingsBusy;

  byId("model-url-row").hidden = !compatible;
  byId("model-url").required = compatible;
  byId("model-key-row").hidden = !usesApi;
  byId("model-api-options").hidden = !usesApi;
  byId("model-api-key").disabled = disabled;
  byId("model-api-key").placeholder = reuseKey ? "已保存 · 留空继续使用" : "粘贴你自己的 API Key";
  byId("model-key-help").textContent = reuseKey ? "这个地址已有保存在本机的 Key。" : "API Key 保存在本机配置中。";
  byId("model-delete-key").hidden = !reuseKey;
  byId("model-delete-key").disabled = disabled;
  byId("model-test").disabled = disabled;
  byId("model-save").disabled = disabled;
  byId("model-provider").disabled = !saved || modelSettingsBusy;
  byId("model-auto").disabled = disabled;
  byId("model-name").disabled = !saved || modelSettingsBusy;
  byId("model-url").disabled = !saved || modelSettingsBusy;
}

function applyModelSettings(settings) {
  savedModelSettings = settings;
  byId("model-provider").value = settings.provider;
  byId("model-name").value = settings.model;
  byId("model-url").value = settings.provider === "compatible" ? settings.base_url : "";
  byId("model-auto").checked = settings.provider !== "ollama" && settings.auto_analyze;
  byId("model-api-key").value = "";
  byId("model-demo-note").hidden = !settings.demo;
  renderModelFields();
}

async function loadModelSettings() {
  byId("model-api-key").value = "";
  feedback("model-form-status");
  applyModelSettings(await request("/api/model-settings"));
}

function modelFormPayload() {
  return {
    provider: byId("model-provider").value,
    model: byId("model-name").value.trim(),
    base_url: byId("model-url").value.trim(),
    api_key: byId("model-api-key").value,
    auto_analyze: byId("model-auto").checked,
  };
}

async function submitModelSettings(action) {
  if (!savedModelSettings || savedModelSettings.demo || modelSettingsBusy) return;
  const form = byId("model-form");
  if (action !== "clear-key" && !form.reportValidity()) return;
  const payload = action === "clear-key" ? {} : modelFormPayload();
  modelSettingsBusy = true;
  renderModelFields();
  feedback("model-form-status", action === "test" ? "正在用固定示例测试连接…" : "");
  try {
    const path = action === "save" ? "/api/model-settings" : `/api/model-settings/${action}`;
    const result = await request(path, payload);
    if (action === "test") {
      feedback("model-form-status", "测试成功。模型返回了预期的结果。");
    } else {
      applyModelSettings(result);
      feedback("model-form-status", action === "save" ? "模型设置已保存。" : "已删除本机保存的 API Key。");
      loadStatus();
    }
  } catch (error) {
    feedback("model-form-status", error.message, true);
  } finally {
    modelSettingsBusy = false;
    renderModelFields();
  }
}

function initModelSettings() {
  byId("model-provider").addEventListener("change", () => {
    const provider = byId("model-provider").value;
    const usingSaved = savedModelSettings?.provider === provider;
    byId("model-name").value = usingSaved ? savedModelSettings.model
      : provider === "ollama" ? "qwen3:4b"
      : provider === "openai" ? savedModelSettings?.default_openai_model ?? "gpt-6-luna" : "";
    byId("model-url").value = provider === "compatible" && usingSaved ? savedModelSettings.base_url : "";
    byId("model-api-key").value = "";
    byId("model-auto").checked = Boolean(usingSaved && provider !== "ollama" && savedModelSettings.auto_analyze);
    feedback("model-form-status");
    renderModelFields();
  });
  byId("model-url").addEventListener("input", renderModelFields);
  byId("model-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitModelSettings("save");
  });
  byId("model-test").addEventListener("click", () => submitModelSettings("test"));
  byId("model-delete-key").addEventListener("click", () => submitModelSettings("clear-key"));
}
