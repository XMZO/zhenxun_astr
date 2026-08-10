const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const LAYER_SELECTORS = {
    wrapper: ".wrapper",
    avatar: ".avatar",
    nickname: ".nickname",
    uid: ".uid",
    calendar: ".rl-img",
    mainCharacter: ".zx-img",
    signCount: ".text-day",
    botMessage: ".text-zx",
    signTag: ".qian",
    title: ".today-text",
    rewardPrimary: '.sign-data > .abs-text:nth-of-type(1) .gift',
    rewardGold: '.sign-data > .abs-text:nth-of-type(2) .gift',
    rewardItem: '.sign-data > .abs-text:nth-of-type(3) .gift',
    divider: ".line",
    current: ".cur-text",
    hearts: ".heart-list",
    level: ".bot-text > p:nth-child(1)",
    attitude: ".bot-text > p:nth-child(2)",
    next: ".bot-text > p:nth-child(3)",
    progressBorder: ".progress-border",
    progressBar: ".progress-bar",
    weather: ".weather-img",
    temperature: ".wd",
    footerCharacter: ".mbl-img",
    date: ".date",
};

const LAYER_GROUPS = [
    {
        title: "顶部信息",
        ids: ["wrapper", "avatar", "nickname", "uid"],
    },
    {
        title: "签到主体",
        ids: ["calendar", "mainCharacter", "signCount", "botMessage", "signTag", "title", "rewardPrimary", "rewardGold", "rewardItem"],
    },
    {
        title: "底部信息",
        ids: ["divider", "current", "hearts", "level", "attitude", "next", "progressBorder", "progressBar", "weather", "temperature", "footerCharacter", "date"],
    },
];

const ASSET_GROUPS = [
    {
        title: "基础图片",
        slots: [
            { key: "avatar", label: "预览头像", fallback: "img/1.png", accept: "image/*", layerId: "avatar" },
            { key: "calendar", label: "日历图", fallback: "img/rl.png", accept: "image/*", layerId: "calendar" },
            { key: "mainCharacter", label: "主角色", fallback: "img/1.png", accept: "image/*", layerId: "mainCharacter" },
            { key: "footerCharacter", label: "底部角色", fallback: "img/2.png", accept: "image/*", layerId: "footerCharacter" },
            { key: "heartEmpty", label: "空心爱心", fallback: "img/h1.png", accept: "image/*", layerId: "hearts" },
            { key: "heartFull", label: "实心爱心", fallback: "img/h2.png", accept: "image/*", layerId: "hearts" },
        ],
    },
    {
        title: "签到标签",
        slots: Array.from({ length: 6 }, (_, index) => ({
            key: `tag:${index}`,
            label: `标签 ${index}`,
            fallback: `img/tag/${index}.png`,
            accept: "image/*",
            compact: true,
            layerId: "signTag",
            previewPath: "preview.tag",
            previewValue: index,
        })),
    },
    {
        title: "天气图标",
        slots: Array.from({ length: 12 }, (_, index) => ({
            key: `weather:${index}`,
            label: `天气 ${index}`,
            fallback: `img/weather/${index}.png`,
            accept: "image/*",
            compact: true,
            layerId: "weather",
            previewPath: "preview.weather",
            previewValue: index,
        })),
    },
    {
        title: "字体（仅 WOFF2）",
        slots: [
            { key: "font:cr105Font", label: "Chill Reunion 105", fallback: "fonts/ChillReunion_105S.woff2", accept: ".woff2,font/woff2", font: true, layerId: "nickname" },
            { key: "font:cr65sFont", label: "Chill Reunion 65", fallback: "fonts/ChillReunion_65S.woff2", accept: ".woff2,font/woff2", font: true, layerId: "botMessage" },
            { key: "font:shFont", label: "思源黑体", fallback: "fonts/SourceHanSansSC-Bold.woff2", accept: ".woff2,font/woff2", font: true, layerId: "signCount" },
            { key: "font:rxxxtFont", label: "rxxxkat", fallback: "fonts/rxxxkat.woff2", accept: ".woff2,font/woff2", font: true, layerId: "current" },
            { key: "font:kcytFont", label: "jcyt", fallback: "fonts/jcyt.woff2", accept: ".woff2,font/woff2", font: true, layerId: "title" },
        ],
    },
];

const ASSET_SLOTS = ASSET_GROUPS.flatMap((group) => group.slots);
const ASSET_SLOT_BY_KEY = new Map(ASSET_SLOTS.map((slot) => [slot.key, slot]));

const FILTER_OPTIONS = [
    ["none", "无"],
    ["brightness(.85)", "压暗"],
    ["brightness(1.15)", "提亮"],
    ["grayscale(1)", "灰度"],
    ["saturate(1.4)", "高饱和"],
    ["sepia(.5)", "复古"],
];

const FONT_OPTIONS = [
    ["cr105Font", "Chill Reunion 105"],
    ["cr65sFont", "Chill Reunion 65"],
    ["shFont", "思源黑体"],
    ["rxxxtFont", "rxxxkat"],
    ["kcytFont", "jcyt"],
    ["sans-serif", "系统无衬线"],
    ["serif", "系统衬线"],
];

let defaultState = null;
let state = null;
let history = [];
let historyIndex = -1;
let selectedLayerId = "wrapper";
let selectedAssetKey = null;
let activePanel = "contentPanel";
let zoom = 0.7;
let renderTimer = null;
let renderSequence = 0;
let toastTimer = null;
let dragging = null;
let resizing = null;
let selectionAnimationFrame = null;

function clone(value) {
    return JSON.parse(JSON.stringify(value));
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function mergeDefaults(defaultValue, value) {
    if (defaultValue && typeof defaultValue === "object" && !Array.isArray(defaultValue)
        && value && typeof value === "object" && !Array.isArray(value)) {
        const merged = clone(defaultValue);
        Object.entries(value).forEach(([key, item]) => {
            merged[key] = key in merged ? mergeDefaults(merged[key], item) : item;
        });
        return merged;
    }
    return value === undefined ? clone(defaultValue) : clone(value);
}

function getPath(root, path) {
    return path.split(".").reduce((current, key) => current?.[key], root);
}

function setPath(root, path, value) {
    const keys = path.split(".");
    const last = keys.pop();
    let current = root;
    keys.forEach((key) => {
        if (!current[key] || typeof current[key] !== "object") current[key] = {};
        current = current[key];
    });
    current[last] = value;
}

function selectedLayer() {
    if (selectedLayerId.startsWith("custom-")) {
        return state.customLayers.find((layer) => layer.id === selectedLayerId) || null;
    }
    return state.layers[selectedLayerId] || null;
}

function layerById(layerId) {
    if (layerId.startsWith("custom-")) {
        return state.customLayers.find((layer) => layer.id === layerId) || null;
    }
    return state.layers[layerId] || null;
}

function pushHistory(before) {
    if (JSON.stringify(before) === JSON.stringify(state)) return false;
    history = history.slice(0, historyIndex + 1);
    history.push(clone(state));
    if (history.length > 80) history.shift();
    historyIndex = history.length - 1;
    saveDraft();
    updateHistoryButtons();
    return true;
}

function updateHistoryTransaction(before, transactionIndex) {
    if (transactionIndex === null || transactionIndex !== historyIndex) {
        return pushHistory(before) ? historyIndex : null;
    }
    if (JSON.stringify(before) === JSON.stringify(state)) {
        history.splice(transactionIndex, 1);
        historyIndex = Math.max(0, transactionIndex - 1);
        saveDraft();
        updateHistoryButtons();
        return null;
    }
    history[transactionIndex] = clone(state);
    saveDraft();
    updateHistoryButtons();
    return transactionIndex;
}

function mutate(mutator, refresh = true) {
    const before = clone(state);
    mutator(state);
    pushHistory(before);
    if (refresh) refreshAll();
    scheduleRender();
}

function saveDraft() {
    try {
        localStorage.setItem("zhenxun-sign-template-editor-draft", JSON.stringify(state));
    } catch (error) {
        console.warn("Unable to save editor draft", error);
    }
}

function updateHistoryButtons() {
    $("#undoButton").disabled = historyIndex <= 0;
    $("#redoButton").disabled = historyIndex >= history.length - 1;
}

function finishActiveControlEdit() {
    const active = document.activeElement;
    if (active?.matches?.("[data-bind], [data-bind-list], [data-layer-field]")) active.blur();
}

function undo() {
    finishActiveControlEdit();
    if (historyIndex <= 0) return;
    historyIndex -= 1;
    state = clone(history[historyIndex]);
    selectedAssetKey = assetKeyForLayer(selectedLayerId);
    saveDraft();
    refreshAll();
    scheduleRender();
}

function redo() {
    finishActiveControlEdit();
    if (historyIndex >= history.length - 1) return;
    historyIndex += 1;
    state = clone(history[historyIndex]);
    selectedAssetKey = assetKeyForLayer(selectedLayerId);
    saveDraft();
    refreshAll();
    scheduleRender();
}

function showToast(message, duration = 2600) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("visible"), duration);
}

function setRenderStatus(message, status) {
    const element = $("#renderStatus");
    element.textContent = message;
    element.className = `status-dot ${status}`;
}

async function apiJson(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
    });
    const payload = await response.json();
    if (!response.ok || payload.error) throw new Error(payload.error || `请求失败 (${response.status})`);
    return payload;
}

function scheduleRender() {
    setRenderStatus("等待刷新", "busy");
    clearTimeout(renderTimer);
    renderTimer = setTimeout(renderPreview, 110);
}

async function renderPreview() {
    const sequence = ++renderSequence;
    setRenderStatus("正在渲染", "busy");
    try {
        const payload = await apiJson("/api/render", {
            method: "POST",
            body: JSON.stringify(state),
        });
        if (sequence !== renderSequence) return;
        const frame = $("#previewFrame");
        frame.onload = () => {
            decoratePreviewFrame();
            setRenderStatus("预览已更新", "ready");
        };
        frame.srcdoc = payload.html;
    } catch (error) {
        if (sequence !== renderSequence) return;
        setRenderStatus(`预览失败: ${error.message}`, "error");
    }
}

function setZoom(nextZoom) {
    zoom = Math.min(1, Math.max(.45, Number(nextZoom) || .7));
    const frame = $("#previewFrame");
    const viewport = $("#canvasViewport");
    frame.style.transform = `scale(${zoom})`;
    viewport.style.width = `${465 * zoom}px`;
    viewport.style.height = `${926 * zoom}px`;
    $("#zoomRange").value = zoom;
    $("#zoomValue").textContent = `${Math.round(zoom * 100)}%`;
    queueSelectionOverlayUpdate();
}

function applyPanelSelection(panelId) {
    activePanel = panelId;
    $$(".panel-tab").forEach((button) => button.classList.toggle("active", button.dataset.panel === panelId));
    $$(".panel-content").forEach((panel) => panel.classList.toggle("active", panel.id === panelId));
}

function syncBoundControls() {
    $$('[data-bind]').forEach((control) => {
        const value = getPath(state, control.dataset.bind);
        if (control.type === "checkbox") control.checked = Boolean(value);
        else if (control.type === "color") control.value = /^#[0-9a-f]{6}$/i.test(String(value)) ? value : "#D47E8F";
        else control.value = value ?? "";
    });
    $$('[data-bind-list]').forEach((control) => {
        const value = getPath(state, control.dataset.bindList);
        control.value = Array.isArray(value) ? value.join("\n") : "";
    });
    $("#modeSignButton").classList.toggle("active", state.preview.mode !== "view");
    $("#modeViewButton").classList.toggle("active", state.preview.mode === "view");
}

function parseControlValue(control) {
    if (control.type === "number" || control.type === "range") return Number(control.value);
    if (control.type === "checkbox") return control.checked;
    return control.value;
}

function bindContentControls() {
    $$('[data-bind], [data-bind-list]').forEach((control) => {
        let before = null;
        let transactionIndex = null;
        const path = control.dataset.bind || control.dataset.bindList;
        const isList = Boolean(control.dataset.bindList);
        const update = () => {
            if (!before) before = clone(state);
            const value = isList
                ? control.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
                : parseControlValue(control);
            setPath(state, path, value);
            transactionIndex = updateHistoryTransaction(before, transactionIndex);
            scheduleRender();
        };
        const finish = () => {
            if (before) {
                before = null;
                transactionIndex = null;
                refreshAll(false);
            }
        };
        control.addEventListener("focus", () => { if (!before) before = clone(state); });
        control.addEventListener("input", update);
        control.addEventListener("change", () => { update(); finish(); });
        control.addEventListener("blur", finish);
    });
}

function populatePreviewSelects() {
    const weather = $("#previewWeather");
    const tag = $("#previewTag");
    weather.innerHTML = Array.from({ length: 12 }, (_, index) => `<option value="${index}">天气 ${index}</option>`).join("");
    tag.innerHTML = Array.from({ length: 6 }, (_, index) => `<option value="${index}">标签 ${index}</option>`).join("");
}

function refreshAll(refreshPanels = true) {
    syncBoundControls();
    if (refreshPanels) {
        renderAssets();
        renderLayers();
        renderInspector();
    }
    updateHistoryButtons();
}

function getAssetOverride(slot) {
    return state.assets?.overrides?.[slot] || null;
}

function getAssetUrl(slot, fallback) {
    return getAssetOverride(slot)?.url || `/source-assets/${fallback}`;
}

function layerIdForAsset(slot) {
    if (!slot?.font) return slot?.layerId || null;
    const fontFamily = slot.key.slice("font:".length);
    const selected = layerById(selectedLayerId);
    if (selected?.kind === "text" && selected.fontFamily === fontFamily && selected.visible !== false) {
        return selectedLayerId;
    }
    const builtInMatch = Object.entries(state.layers).find(([, layer]) => (
        layer.kind === "text" && layer.fontFamily === fontFamily && layer.visible !== false
    ));
    if (builtInMatch) return builtInMatch[0];
    const customMatch = state.customLayers.find((layer) => (
        layer.kind === "text" && layer.fontFamily === fontFamily && layer.visible !== false
    ));
    return customMatch?.id || slot.layerId || null;
}

function assetKeyForLayer(layerId) {
    const directAssets = {
        avatar: "avatar",
        calendar: "calendar",
        mainCharacter: "mainCharacter",
        footerCharacter: "footerCharacter",
        signTag: `tag:${Number(state.preview.tag) || 0}`,
        weather: `weather:${Number(state.preview.weather) || 0}`,
    };
    if (directAssets[layerId]) return directAssets[layerId];
    if (layerId === "hearts") {
        if (selectedAssetKey === "heartEmpty" || selectedAssetKey === "heartFull") return selectedAssetKey;
        return Number(state.content.filledHearts) > 0 ? "heartFull" : "heartEmpty";
    }
    const layer = layerById(layerId);
    const fontKey = layer?.kind === "text" ? `font:${layer.fontFamily}` : null;
    return fontKey && ASSET_SLOT_BY_KEY.has(fontKey) ? fontKey : null;
}

function selectAsset(assetKey) {
    const slot = ASSET_SLOT_BY_KEY.get(assetKey);
    if (!slot) return;
    const layerId = layerIdForAsset(slot);
    if (!layerId || !layerById(layerId)) return;
    let previewChanged = false;
    if (slot.previewPath && getPath(state, slot.previewPath) !== slot.previewValue) {
        const before = clone(state);
        setPath(state, slot.previewPath, slot.previewValue);
        pushHistory(before);
        previewChanged = true;
    }
    selectedLayerId = layerId;
    selectedAssetKey = assetKey;
    syncBoundControls();
    renderAssets();
    renderLayers();
    renderInspector();
    syncFrameSelection();
    if (previewChanged) scheduleRender();
}

function renderAssets() {
    const root = $("#assetGroups");
    root.innerHTML = `<div class="asset-groups">${ASSET_GROUPS.map((group) => `
        <section class="asset-group">
            <div class="asset-group-title">${escapeHtml(group.title)}</div>
            <div class="asset-list">${group.slots.map((slot) => renderAssetRow(slot)).join("")}</div>
        </section>`).join("")}</div>`;
    $$("[data-asset-upload]", root).forEach((input) => {
        input.addEventListener("change", () => {
            const file = input.files?.[0];
            if (file) uploadAsset(input.dataset.assetUpload, file);
            input.value = "";
        });
    });
    $$("[data-asset-select]", root).forEach((row) => {
        const activate = (event) => {
            if (event.target.closest(".asset-actions")) return;
            selectAsset(row.dataset.assetSelect);
        };
        row.addEventListener("click", activate);
        row.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            activate(event);
        });
    });
    $$("[data-asset-reset]", root).forEach((button) => {
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            resetAsset(button.dataset.assetReset);
        });
    });
}

function renderAssetRow(slot) {
    const override = getAssetOverride(slot.key);
    const fallbackUrl = `/source-assets/${slot.fallback}`;
    const image = slot.font
        ? `<span class="font-chip">W2</span>`
        : `<img src="${escapeHtml(override?.url || fallbackUrl)}" alt="">`;
    const selected = selectedAssetKey === slot.key;
    const meta = `${override ? "已替换 · 编辑器工作区" : "原版素材"}${selected ? " · 已定位" : ""}`;
    const reset = override ? `<button class="asset-action" data-asset-reset="${escapeHtml(slot.key)}" title="恢复原版">↺</button>` : "";
    return `<div class="asset-row ${slot.compact ? "compact" : ""} ${selected ? "selected" : ""}" data-asset-select="${escapeHtml(slot.key)}" role="button" tabindex="0" aria-pressed="${selected}">
        <div class="asset-thumb">${image}</div>
        <div class="asset-label"><div class="asset-name">${escapeHtml(slot.label)}</div><div class="asset-meta">${meta}</div></div>
        <div class="asset-actions">
            <label class="asset-action" title="上传替换">换<input data-asset-upload="${escapeHtml(slot.key)}" type="file" accept="${escapeHtml(slot.accept)}"></label>
            ${reset}
        </div>
    </div>`;
}

function renderLayers() {
    const root = $("#layerList");
    let markup = "";
    LAYER_GROUPS.forEach((group) => {
        markup += `<div class="layer-group-label">${escapeHtml(group.title)}</div>`;
        group.ids.forEach((id) => {
            const layer = state.layers[id];
            if (!layer) return;
            markup += renderLayerRow(id, layer, false);
        });
    });
    if (state.customLayers.length) {
        markup += `<div class="layer-group-label">自定义图层</div>`;
        state.customLayers.slice().reverse().forEach((layer) => {
            markup += renderLayerRow(layer.id, layer, true);
        });
    }
    root.innerHTML = markup;
    $$("[data-layer-select]", root).forEach((row) => {
        row.addEventListener("click", () => selectLayer(row.dataset.layerSelect));
    });
    $$("[data-layer-toggle]", root).forEach((button) => {
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            const layer = layerById(button.dataset.layerToggle);
            if (!layer) return;
            mutate((current) => {
                const target = layerByIdFromState(current, button.dataset.layerToggle);
                target.visible = !target.visible;
            });
        });
    });
    $$("[data-custom-delete]", root).forEach((button) => {
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            deleteCustomLayer(button.dataset.customDelete);
        });
    });
}

function layerByIdFromState(sourceState, layerId) {
    if (layerId.startsWith("custom-")) return sourceState.customLayers.find((layer) => layer.id === layerId);
    return sourceState.layers[layerId];
}

function renderLayerRow(id, layer, custom) {
    const visibleIcon = layer.visible ? "◉" : "○";
    const deleteButton = custom ? `<button class="layer-visibility custom-delete" data-custom-delete="${escapeHtml(id)}" title="删除">×</button>` : "";
    return `<div class="layer-row ${selectedLayerId === id ? "selected" : ""}" data-layer-select="${escapeHtml(id)}">
        <span class="layer-row-name">${escapeHtml(layer.label || id)}</span>
        <span class="layer-row-tools"><button class="layer-visibility ${layer.visible ? "" : "hidden"}" data-layer-toggle="${escapeHtml(id)}" title="显示/隐藏">${visibleIcon}</button>${deleteButton}</span>
    </div>`;
}

function selectLayer(layerId) {
    if (!layerById(layerId)) return;
    selectedLayerId = layerId;
    selectedAssetKey = assetKeyForLayer(layerId);
    renderAssets();
    renderLayers();
    renderInspector();
    syncFrameSelection();
}

function numberField(label, field, value, min = -2000, max = 2000, step = 1, wide = false) {
    return `<label class="inspector-field ${wide ? "wide" : ""}"><span class="inspector-label">${label}</span><input data-layer-field="${field}" type="number" value="${escapeHtml(value ?? "")}" min="${min}" max="${max}" step="${step}"></label>`;
}

function selectField(label, field, value, options, wide = false) {
    return `<label class="inspector-field ${wide ? "wide" : ""}"><span class="inspector-label">${label}</span><select data-layer-field="${field}">${options.map(([optionValue, optionLabel]) => `<option value="${escapeHtml(optionValue)}" ${String(value) === optionValue ? "selected" : ""}>${escapeHtml(optionLabel)}</option>`).join("")}</select></label>`;
}

function textField(label, field, value, wide = false) {
    return `<label class="inspector-field ${wide ? "wide" : ""}"><span class="inspector-label">${label}</span><input data-layer-field="${field}" type="text" value="${escapeHtml(value ?? "")}"></label>`;
}

function colorField(label, field, value, wide = false) {
    const safeValue = /^#[0-9a-f]{6}$/i.test(String(value || "")) ? value : "#D47E8F";
    return `<label class="inspector-field ${wide ? "wide" : ""}"><span class="inspector-label">${label}</span><input data-layer-field="${field}" data-color-fallback="${safeValue}" type="color" value="${safeValue}"></label>`;
}

function renderInspector() {
    const title = $("#inspectorTitle");
    const root = $("#inspectorFields");
    const layer = selectedLayer();
    if (!layer) {
        title.textContent = "未选择图层";
        root.innerHTML = `<div class="empty-inspector">从左侧图层列表或画布选择一个元素。</div>`;
        return;
    }
    title.textContent = layer.label || selectedLayerId;
    const isCustom = selectedLayerId.startsWith("custom-");
    const kind = layer.kind === "image" ? "image" : layer.kind === "text" ? "text" : "box";
    let markup = `<section class="inspector-section">
        <div class="visibility-row"><span>显示此图层</span><input data-layer-field="visible" type="checkbox" ${layer.visible ? "checked" : ""}></div>
        <div class="inspector-grid">
            ${numberField("X 偏移", "x", layer.x, -2000, 2000, 1)}
            ${numberField("Y 偏移", "y", layer.y, -2000, 2000, 1)}
            ${numberField("宽度", "width", layer.width, 1, 3000, 1)}
            ${numberField("高度", "height", layer.height, 1, 3000, 1)}
            ${numberField("旋转角度", "rotation", layer.rotation, -360, 360, 1)}
            ${numberField("层级", "zIndex", layer.zIndex, -9999, 9999, 1)}
        </div>
        <label class="inspector-field"><span class="inspector-label">透明度 <output class="range-value" data-range-output="opacity">${Math.round(Number(layer.opacity ?? 1) * 100)}%</output></span><div class="range-line"><input data-layer-field="opacity" type="range" min="0" max="1" step="0.01" value="${layer.opacity ?? 1}"><span></span></div></label>
        <div class="inspector-grid">
            ${numberField("横向缩放", "scaleX", layer.scaleX, .01, 20, .01)}
            ${numberField("纵向缩放", "scaleY", layer.scaleY, .01, 20, .01)}
        </div>
    </section>`;

    if (isCustom && kind === "text") {
        markup += `<section class="inspector-section"><div class="inspector-section-title">文字内容</div><label class="inspector-field"><span class="inspector-label">文字</span><textarea data-layer-field="text" rows="4">${escapeHtml(layer.text || "")}</textarea></label></section>`;
    }
    if (isCustom && kind === "image") {
        markup += `<section class="inspector-section"><div class="inspector-section-title">自定义图片</div><div class="custom-image-picker"><div class="asset-thumb"><img src="${escapeHtml(layer.imageRef?.url || "/source-assets/img/1.png")}" alt=""></div><label class="button small file-button">上传图片<input id="customLayerUpload" type="file" accept="image/*"></label></div></section>`;
    }

    if (kind === "text" || isCustom && kind === "text") {
        markup += `<section class="inspector-section"><div class="inspector-section-title">文字样式</div><div class="inspector-grid">
            ${numberField("字号", "fontSize", layer.fontSize, 1, 300, 1)}
            ${selectField("字体", "fontFamily", layer.fontFamily, FONT_OPTIONS)}
            ${selectField("字重", "fontWeight", layer.fontWeight, [["normal", "常规"], ["600", "半粗"], ["bold", "粗体"], ["800", "特粗"]])}
            ${numberField("行高", "lineHeight", layer.lineHeight, .2, 5, .05)}
            ${numberField("字间距", "letterSpacing", layer.letterSpacing, -20, 100, .5)}
            ${selectField("对齐", "textAlign", layer.textAlign, [["left", "左对齐"], ["center", "居中"], ["right", "右对齐"]])}
            ${textField("文字颜色", "color", layer.color, true)}
            ${textField("背景颜色", "backgroundColor", layer.backgroundColor, true)}
        </div></section>`;
    } else {
        markup += `<section class="inspector-section"><div class="inspector-section-title">外观</div><div class="inspector-grid">
            ${textField("背景颜色", "backgroundColor", layer.backgroundColor, true)}
            ${numberField("圆角", "borderRadius", layer.borderRadius, 0, 1000, 1)}
            ${selectField("滤镜", "filter", layer.filter, FILTER_OPTIONS)}
            ${textField("阴影", "boxShadow", layer.boxShadow, true)}
            ${kind === "image" ? selectField("图片填充", "objectFit", layer.objectFit, [["contain", "完整显示"], ["cover", "裁切填充"], ["fill", "拉伸"], ["none", "原始尺寸"]], true) : ""}
        </div></section>`;
    }
    if (isCustom) {
        markup += `<section class="inspector-section"><button id="deleteSelectedCustom" class="button danger">删除这个自定义图层</button></section>`;
    }
    root.innerHTML = markup;
    bindInspectorFields();
    const customUpload = $("#customLayerUpload", root);
    if (customUpload) {
        customUpload.addEventListener("change", () => {
            const file = customUpload.files?.[0];
            if (file) uploadCustomLayerImage(selectedLayerId, file);
        });
    }
    const deleteButton = $("#deleteSelectedCustom", root);
    if (deleteButton) deleteButton.addEventListener("click", () => deleteCustomLayer(selectedLayerId));
}

function bindInspectorFields() {
    $$('[data-layer-field]').forEach((control) => {
        let before = null;
        let transactionIndex = null;
        const field = control.dataset.layerField;
        const update = () => {
            if (!before) before = clone(state);
            const layer = layerById(selectedLayerId);
            if (!layer) return;
            let value;
            if (control.type === "checkbox") value = control.checked;
            else if (control.type === "number" || control.type === "range") value = Number(control.value);
            else value = control.value;
            layer[field] = value;
            const output = $(`[data-range-output="${field}"]`);
            if (output) output.textContent = field === "opacity" ? `${Math.round(value * 100)}%` : String(value);
            const frameElement = findFrameLayerElement(selectedLayerId);
            if (frameElement) {
                applyLayerStyle(frameElement, layer);
                if (field === "text" && layer.kind === "text") frameElement.textContent = layer.text;
                queueSelectionOverlayUpdate();
            }
            transactionIndex = updateHistoryTransaction(before, transactionIndex);
            scheduleRender();
        };
        const finish = () => {
            if (before) {
                before = null;
                transactionIndex = null;
                renderLayers();
            }
        };
        control.addEventListener("focus", () => { if (!before) before = clone(state); });
        control.addEventListener("input", update);
        control.addEventListener("change", () => { update(); finish(); });
        control.addEventListener("blur", finish);
    });
}

function resetSelectedLayer() {
    if (selectedLayerId.startsWith("custom-")) {
        deleteCustomLayer(selectedLayerId);
        return;
    }
    mutate((current) => {
        current.layers[selectedLayerId] = clone(defaultState.layers[selectedLayerId]);
    });
    showToast("当前图层已恢复默认");
}

function addCustomLayer(kind) {
    const before = clone(state);
    const id = `custom-${kind}-${Date.now().toString(36)}`;
    const layer = {
        id,
        label: kind === "text" ? "自定义文字" : "自定义图片",
        kind,
        text: kind === "text" ? "自定义文字" : "",
        imageRef: null,
        x: 40,
        y: kind === "text" ? 120 : 220,
        width: kind === "text" ? 260 : 130,
        height: kind === "text" ? 55 : 160,
        rotation: 0,
        scaleX: 1,
        scaleY: 1,
        opacity: 1,
        zIndex: 1200,
        visible: true,
        backgroundColor: "",
        borderRadius: 0,
        fontSize: 28,
        fontFamily: "cr105Font",
        fontWeight: "normal",
        lineHeight: 1.2,
        letterSpacing: 0,
        textAlign: "left",
        color: "#D47E8F",
        objectFit: "contain",
        filter: "none",
        boxShadow: "",
    };
    state.customLayers.push(layer);
    selectedLayerId = id;
    selectedAssetKey = assetKeyForLayer(id);
    pushHistory(before);
    applyPanelSelection("layersPanel");
    refreshAll();
    scheduleRender();
}

function deleteCustomLayer(layerId) {
    if (!layerId.startsWith("custom-")) return;
    const layer = layerById(layerId);
    if (!layer || !window.confirm(`删除“${layer.label}”？`)) return;
    mutate((current) => {
        current.customLayers = current.customLayers.filter((item) => item.id !== layerId);
    });
    selectedLayerId = "wrapper";
    selectedAssetKey = null;
    refreshAll();
}

function resetAsset(slot) {
    if (!getAssetOverride(slot)) return;
    mutate((current) => {
        delete current.assets.overrides[slot];
    });
    showToast("素材已恢复原版");
}

function readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("无法读取文件"));
        reader.readAsDataURL(file);
    });
}

async function uploadAsset(slot, file) {
    if (file.size > 16 * 1024 * 1024) {
        showToast("素材不能超过 16MB");
        return;
    }
    const before = clone(state);
    try {
        showToast("正在处理素材…", 5000);
        const data = await readAsDataUrl(file);
        const payload = await apiJson("/api/upload", {
            method: "POST",
            body: JSON.stringify({ slot, name: file.name, mime: file.type, data }),
        });
        state.assets.overrides[slot] = payload.override;
        pushHistory(before);
        refreshAll();
        scheduleRender();
        showToast("素材已加入编辑器工作区");
    } catch (error) {
        showToast(`上传失败: ${error.message}`, 5000);
    }
}

async function uploadCustomLayerImage(layerId, file) {
    const layer = layerById(layerId);
    if (!layer) return;
    if (file.size > 16 * 1024 * 1024) {
        showToast("图片不能超过 16MB");
        return;
    }
    const before = clone(state);
    try {
        const data = await readAsDataUrl(file);
        const payload = await apiJson("/api/upload", {
            method: "POST",
            body: JSON.stringify({ slot: `custom:${layerId}`, name: file.name, mime: file.type, data }),
        });
        layer.imageRef = payload.override;
        pushHistory(before);
        refreshAll();
        scheduleRender();
        showToast("自定义图片已更新");
    } catch (error) {
        showToast(`上传失败: ${error.message}`, 5000);
    }
}

function decoratePreviewFrame() {
    const frame = $("#previewFrame");
    const documentInFrame = frame.contentDocument;
    if (!documentInFrame) return;
    const style = documentInFrame.createElement("style");
    style.textContent = `
        [data-editor-layer] { cursor: move; }
        [data-editor-layer]:hover { outline: 1px dashed rgba(255, 255, 255, .95) !important; outline-offset: 1px; }
        [data-editor-layer][data-editor-selected="true"] { outline: 2px solid rgba(255, 79, 135, .9) !important; outline-offset: 1px; }
    `;
    documentInFrame.head.appendChild(style);
    Object.entries(LAYER_SELECTORS).forEach(([layerId, selector]) => {
        const element = documentInFrame.querySelector(selector);
        if (element) attachFrameElement(element, layerId);
    });
    documentInFrame.querySelectorAll("[data-editor-layer]").forEach((element) => {
        const layerId = element.dataset.editorLayer;
        if (layerId?.startsWith("custom-")) attachFrameElement(element, layerId);
    });
    syncFrameSelection();
    documentInFrame.querySelectorAll("img").forEach((image) => {
        if (!image.complete) image.addEventListener("load", queueSelectionOverlayUpdate, { once: true });
    });
    if (documentInFrame.fonts?.ready) {
        documentInFrame.fonts.ready.then(queueSelectionOverlayUpdate).catch(() => {});
    }
}

function findFrameLayerElement(layerId) {
    const documentInFrame = $("#previewFrame").contentDocument;
    if (!documentInFrame) return null;
    return Array.from(documentInFrame.querySelectorAll("[data-editor-layer]"))
        .find((element) => element.dataset.editorLayer === layerId) || null;
}

function syncFrameSelection() {
    const documentInFrame = $("#previewFrame").contentDocument;
    if (!documentInFrame) {
        hideSelectionOverlay();
        return;
    }
    documentInFrame.querySelectorAll("[data-editor-layer]").forEach((element) => {
        element.dataset.editorSelected = element.dataset.editorLayer === selectedLayerId ? "true" : "false";
    });
    queueSelectionOverlayUpdate();
}

function queueSelectionOverlayUpdate() {
    if (selectionAnimationFrame !== null) cancelAnimationFrame(selectionAnimationFrame);
    selectionAnimationFrame = requestAnimationFrame(() => {
        selectionAnimationFrame = null;
        updateSelectionOverlay();
    });
}

function hideSelectionOverlay() {
    const overlay = $("#selectionOverlay");
    if (overlay) overlay.hidden = true;
}

function updateSelectionOverlay() {
    const overlay = $("#selectionOverlay");
    const label = $("#selectionLabel");
    const layer = state ? layerById(selectedLayerId) : null;
    const element = findFrameLayerElement(selectedLayerId);
    if (!overlay || !label || !layer || !element || layer.visible === false) {
        hideSelectionOverlay();
        return;
    }
    const documentInFrame = element.ownerDocument;
    const computedStyle = documentInFrame.defaultView?.getComputedStyle(element);
    const bounds = element.getBoundingClientRect();
    if (computedStyle?.display === "none" || computedStyle?.visibility === "hidden"
        || bounds.width <= 0 || bounds.height <= 0) {
        hideSelectionOverlay();
        return;
    }
    overlay.style.left = `${bounds.left * zoom}px`;
    overlay.style.top = `${bounds.top * zoom}px`;
    overlay.style.width = `${bounds.width * zoom}px`;
    overlay.style.height = `${bounds.height * zoom}px`;
    overlay.classList.toggle("resizable", canResizeLayer(layer));
    label.textContent = `${layer.label || selectedLayerId}  X ${Math.round(bounds.left)}  Y ${Math.round(bounds.top)}  ${Math.round(bounds.width)} × ${Math.round(bounds.height)}`;
    overlay.hidden = false;
}

function canResizeLayer(layer) {
    return selectedLayerId !== "wrapper"
        && Number.isFinite(Number(layer?.width))
        && Number.isFinite(Number(layer?.height));
}

function attachFrameElement(element, layerId) {
    element.dataset.editorLayer = layerId;
    element.dataset.editorSelected = selectedLayerId === layerId ? "true" : "false";
    element.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        selectLayer(layerId);
    });
    element.addEventListener("pointerdown", (event) => startDrag(event, layerId, element));
}

function layerTransform(layer) {
    return `translate(${Number(layer.x || 0)}px, ${Number(layer.y || 0)}px) rotate(${Number(layer.rotation || 0)}deg) scale(${Number(layer.scaleX || 1)}, ${Number(layer.scaleY || 1)})`;
}

function applyLayerStyle(element, layer) {
    element.style.setProperty("transform", layerTransform(layer), "important");
    element.style.setProperty("opacity", Number(layer.opacity ?? 1), "important");
    element.style.setProperty("z-index", Number(layer.zIndex ?? 0), "important");
    element.style.setProperty("display", layer.visible === false ? "none" : "", "important");
    if (layer.width != null) element.style.setProperty("width", `${Number(layer.width)}px`, "important");
    if (layer.height != null) element.style.setProperty("height", `${Number(layer.height)}px`, "important");
    if (element.classList.contains("avatar")) element.style.setProperty("flex-shrink", "0", "important");
    if (layer.fontSize != null) element.style.setProperty("font-size", `${Number(layer.fontSize)}px`, "important");
    if (layer.color) element.style.setProperty("color", layer.color, "important");
    if (layer.backgroundColor) element.style.setProperty("background-color", layer.backgroundColor, "important");
    if (layer.boxShadow != null) element.style.setProperty("box-shadow", layer.boxShadow || "none", "important");
    if (layer.borderRadius != null) element.style.setProperty("border-radius", `${Number(layer.borderRadius)}px`, "important");
    if (layer.kind === "text") {
        element.style.setProperty("font-family", `'${layer.fontFamily || "cr105Font"}'`, "important");
        element.style.setProperty("font-weight", layer.fontWeight || "normal", "important");
        if (layer.lineHeight != null) element.style.setProperty("line-height", Number(layer.lineHeight), "important");
        element.style.setProperty("letter-spacing", `${Number(layer.letterSpacing || 0)}px`, "important");
        element.style.setProperty("text-align", layer.textAlign || "left", "important");
    }
    if (layer.kind === "image") {
        element.style.setProperty("object-fit", layer.objectFit || "contain", "important");
        element.style.setProperty("filter", layer.filter || "none", "important");
    }
}

function resizeAnchor(bounds, direction) {
    return {
        x: direction.includes("e")
            ? bounds.left
            : direction.includes("w") ? bounds.right : bounds.left + bounds.width / 2,
        y: direction.includes("s")
            ? bounds.top
            : direction.includes("n") ? bounds.bottom : bounds.top + bounds.height / 2,
    };
}

function startResize(event) {
    if (event.button !== 0 || resizing || dragging) return;
    const direction = event.currentTarget.dataset.resizeHandle;
    const layer = selectedLayer();
    const element = findFrameLayerElement(selectedLayerId);
    if (!direction || !layer || !element || !canResizeLayer(layer)) return;
    event.preventDefault();
    event.stopPropagation();
    finishActiveControlEdit();
    const bounds = element.getBoundingClientRect();
    resizing = {
        layerId: selectedLayerId,
        element,
        handle: event.currentTarget,
        pointerId: event.pointerId,
        direction,
        before: clone(state),
        startX: event.clientX,
        startY: event.clientY,
        x: Number(layer.x || 0),
        y: Number(layer.y || 0),
        width: Math.max(4, Number(layer.width) || bounds.width),
        height: Math.max(4, Number(layer.height) || bounds.height),
        rotation: Number(layer.rotation || 0) * Math.PI / 180,
        scaleX: Math.max(.01, Math.abs(Number(layer.scaleX || 1))),
        scaleY: Math.max(.01, Math.abs(Number(layer.scaleY || 1))),
        anchor: resizeAnchor(bounds, direction),
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    document.body.classList.add("is-resizing");
    document.addEventListener("pointermove", moveResize);
    document.addEventListener("pointerup", finishResize);
    document.addEventListener("pointercancel", cancelResize);
}

function moveResize(event) {
    if (!resizing || event.pointerId !== resizing.pointerId) return;
    const target = layerById(resizing.layerId);
    if (!target) return;
    const deltaX = (event.clientX - resizing.startX) / zoom;
    const deltaY = (event.clientY - resizing.startY) / zoom;
    const cos = Math.cos(resizing.rotation);
    const sin = Math.sin(resizing.rotation);
    const localDeltaX = deltaX * cos + deltaY * sin;
    const localDeltaY = -deltaX * sin + deltaY * cos;
    let width = resizing.width;
    let height = resizing.height;
    if (resizing.direction.includes("e")) width += localDeltaX / resizing.scaleX;
    if (resizing.direction.includes("w")) width -= localDeltaX / resizing.scaleX;
    if (resizing.direction.includes("s")) height += localDeltaY / resizing.scaleY;
    if (resizing.direction.includes("n")) height -= localDeltaY / resizing.scaleY;
    if (event.shiftKey && /[ew]/.test(resizing.direction) && /[ns]/.test(resizing.direction)) {
        const ratio = resizing.width / resizing.height;
        const widthChange = Math.abs(width - resizing.width) / resizing.width;
        const heightChange = Math.abs(height - resizing.height) / resizing.height;
        if (widthChange >= heightChange) height = width / ratio;
        else width = height * ratio;
    }
    target.width = Math.max(4, Math.round(width));
    target.height = Math.max(4, Math.round(height));
    target.x = resizing.x;
    target.y = resizing.y;
    applyLayerStyle(resizing.element, target);
    const currentAnchor = resizeAnchor(resizing.element.getBoundingClientRect(), resizing.direction);
    target.x = Math.round(resizing.x + resizing.anchor.x - currentAnchor.x);
    target.y = Math.round(resizing.y + resizing.anchor.y - currentAnchor.y);
    applyLayerStyle(resizing.element, target);
    updateInspectorGeometry(target);
    queueSelectionOverlayUpdate();
}

function removeResizeListeners() {
    document.removeEventListener("pointermove", moveResize);
    document.removeEventListener("pointerup", finishResize);
    document.removeEventListener("pointercancel", cancelResize);
    document.body.classList.remove("is-resizing");
}

function finishResize(event) {
    if (!resizing || event?.pointerId !== resizing.pointerId) return;
    const session = resizing;
    resizing = null;
    removeResizeListeners();
    if (session.handle.hasPointerCapture?.(session.pointerId)) {
        session.handle.releasePointerCapture(session.pointerId);
    }
    pushHistory(session.before);
    renderLayers();
    scheduleRender();
}

function cancelResize(event) {
    if (!resizing || event?.pointerId && event.pointerId !== resizing.pointerId) return;
    const session = resizing;
    resizing = null;
    removeResizeListeners();
    if (session.handle.hasPointerCapture?.(session.pointerId)) {
        session.handle.releasePointerCapture(session.pointerId);
    }
    state = clone(session.before);
    const restored = layerById(session.layerId);
    if (restored) applyLayerStyle(session.element, restored);
    refreshAll();
    queueSelectionOverlayUpdate();
    scheduleRender();
}

function bindResizeHandles() {
    $$("[data-resize-handle]").forEach((handle) => {
        handle.addEventListener("pointerdown", startResize);
    });
}

function startDrag(event, layerId, element) {
    if (event.button !== 0) return;
    const layer = layerById(layerId);
    if (!layer) return;
    event.preventDefault();
    event.stopPropagation();
    selectLayer(layerId);
    dragging = {
        layerId,
        element,
        before: clone(state),
        startX: event.clientX,
        startY: event.clientY,
        x: Number(layer.x || 0),
        y: Number(layer.y || 0),
    };
    const documentInFrame = $("#previewFrame").contentDocument;
    const move = (moveEvent) => {
        if (!dragging) return;
        const target = layerById(layerId);
        if (!target) return;
        target.x = Math.round(dragging.x + (moveEvent.clientX - dragging.startX) / zoom);
        target.y = Math.round(dragging.y + (moveEvent.clientY - dragging.startY) / zoom);
        applyLayerStyle(element, target);
        updateInspectorGeometry(target);
        queueSelectionOverlayUpdate();
    };
    const end = () => {
        if (!dragging) return;
        documentInFrame?.removeEventListener("pointermove", move);
        documentInFrame?.removeEventListener("pointerup", end);
        const before = dragging.before;
        dragging = null;
        pushHistory(before);
        renderLayers();
        scheduleRender();
    };
    documentInFrame?.addEventListener("pointermove", move);
    documentInFrame?.addEventListener("pointerup", end);
}

function updateInspectorGeometry(layer) {
    ["x", "y", "width", "height"].forEach((field) => {
        const control = $(`[data-layer-field="${field}"]`, $("#inspectorFields"));
        if (control) control.value = layer[field];
    });
}

function makeProjectDownload(payload, filename) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

async function exportProject() {
    try {
        const payload = await apiJson("/api/project/export", { method: "POST", body: JSON.stringify(state) });
        makeProjectDownload(payload, "zhenxun-sign-template-project.json");
        showToast("项目已导出");
    } catch (error) {
        showToast(`导出失败: ${error.message}`, 5000);
    }
}

async function importProject(file) {
    try {
        const payload = JSON.parse(await file.text());
        const result = await apiJson("/api/project/import", { method: "POST", body: JSON.stringify(payload) });
        const before = clone(state);
        state = mergeDefaults(defaultState, result.state);
        history = [clone(state)];
        historyIndex = 0;
        selectedLayerId = "wrapper";
        selectedAssetKey = null;
        saveDraft();
        refreshAll();
        scheduleRender();
        showToast("项目已导入");
        void before;
    } catch (error) {
        showToast(`导入失败: ${error.message}`, 5000);
    }
}

async function generateTemplate() {
    const button = $("#generateButton");
    button.disabled = true;
    button.textContent = "生成中…";
    try {
        const result = await apiJson("/api/generate", { method: "POST", body: JSON.stringify(state) });
        $("#generateResult").innerHTML = `<p>模板 <strong>${escapeHtml(result.pack_name)}</strong> 已自动安装到插件的 <code>template_packs</code> 目录。</p><p>管理员在 AstrBot 中发送：</p><p><code>切换签到模板 ${escapeHtml(result.pack_name)}</code></p><p>迁移到其他实例：下载 ZIP，在“插件配置 -> 签到模板管理”中上传并保存。</p><p class="warning-line">内部随机 ID 仅用于唯一识别，不会显示在普通操作界面；输出副本保存在 ${escapeHtml(result.directory)}。</p><a href="${escapeHtml(result.download)}" download>下载模板 ZIP</a>`;
        $("#generateDialog").showModal();
        showToast(`模板 ${result.pack_name} 已生成并安装`);
    } catch (error) {
        showToast(`生成失败: ${error.message}`, 6000);
    } finally {
        button.disabled = false;
        button.textContent = "生成模板";
    }
}

function resetToOriginal() {
    if (!window.confirm("恢复原版会丢弃当前编辑器草稿，但不会改动现有插件文件。继续吗？")) return;
    state = clone(defaultState);
    history = [clone(state)];
    historyIndex = 0;
    selectedLayerId = "wrapper";
    selectedAssetKey = null;
    saveDraft();
    refreshAll();
    scheduleRender();
    showToast("已恢复原版基线");
}

function addKeyboardShortcuts() {
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && resizing) {
            event.preventDefault();
            cancelResize();
            return;
        }
        if (!(event.ctrlKey || event.metaKey)) return;
        if (event.key.toLowerCase() === "z" && !event.shiftKey) {
            event.preventDefault();
            undo();
        } else if (event.key.toLowerCase() === "y" || (event.key.toLowerCase() === "z" && event.shiftKey)) {
            event.preventDefault();
            redo();
        } else if (event.key.toLowerCase() === "s") {
            event.preventDefault();
            exportProject();
        }
    });
}

function bindToolbar() {
    $("#undoButton").addEventListener("click", undo);
    $("#redoButton").addEventListener("click", redo);
    $("#resetButton").addEventListener("click", resetToOriginal);
    $("#exportProjectButton").addEventListener("click", exportProject);
    $("#importProjectInput").addEventListener("change", () => {
        const file = $("#importProjectInput").files?.[0];
        if (file) importProject(file);
        $("#importProjectInput").value = "";
    });
    $("#generateButton").addEventListener("click", generateTemplate);
    $("#closeDialogButton").addEventListener("click", () => $("#generateDialog").close());
    $("#zoomRange").addEventListener("input", (event) => setZoom(event.target.value));
    $("#zoomOutButton").addEventListener("click", () => setZoom(zoom - .05));
    $("#zoomInButton").addEventListener("click", () => setZoom(zoom + .05));
    $("#modeSignButton").addEventListener("click", () => {
        mutate((current) => { current.preview.mode = "sign"; });
    });
    $("#modeViewButton").addEventListener("click", () => {
        mutate((current) => { current.preview.mode = "view"; });
    });
    $("#addTextLayer").addEventListener("click", () => addCustomLayer("text"));
    $("#addImageLayer").addEventListener("click", () => addCustomLayer("image"));
    $("#resetLayerButton").addEventListener("click", resetSelectedLayer);
    $$(".panel-tab").forEach((button) => button.addEventListener("click", () => applyPanelSelection(button.dataset.panel)));
}

async function init() {
    try {
        defaultState = await apiJson("/api/default-state");
        const saved = localStorage.getItem("zhenxun-sign-template-editor-draft");
        if (saved) {
            try {
                state = mergeDefaults(defaultState, JSON.parse(saved));
                showToast("已载入上次编辑草稿", 2200);
            } catch {
                state = clone(defaultState);
            }
        } else {
            state = clone(defaultState);
        }
        history = [clone(state)];
        historyIndex = 0;
        populatePreviewSelects();
        bindContentControls();
        bindToolbar();
        bindResizeHandles();
        addKeyboardShortcuts();
        setZoom(zoom);
        refreshAll();
        scheduleRender();
    } catch (error) {
        setRenderStatus(`编辑器启动失败: ${error.message}`, "error");
    }
}

init();
