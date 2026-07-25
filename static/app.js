const state = {
  token: sessionStorage.getItem("tgm-portal-token") || "",
  csrf: "",
  timer: null,
  currentNode: "",
  currentNodeDetail: null,
  selectedNode: null,
  selectedNodes: [],
  nodes: [],
  pendingNode: "",
  nodeMode: "",
  nodePartition: "",
  nodeSearch: "",
  nodeSort: "node",
  requestMode: "available",
};

const MAX_SELECTED_NODES = 32;

const $ = (selector) => document.querySelector(selector);

function toast(message, type = "success") {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  $("#toast-region").append(item);
  window.setTimeout(() => item.remove(), 4600);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body) {
    headers.set("Content-Type", "application/json");
  }
  if (options.method && options.method !== "GET" && path !== "/api/auth") {
    headers.set("X-CSRF-Token", state.csrf);
  }
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `요청 실패 (${response.status})`);
  }
  return payload;
}

function setConnection(online, message) {
  const dot = $("#connection-dot");
  dot.classList.toggle("online", online);
  dot.classList.toggle("offline", !online);
  $("#connection-text").textContent = message;
}

function setUnlocked(unlocked) {
  $("#unlock").classList.toggle("is-hidden", unlocked);
  $("#app").classList.toggle("is-hidden", !unlocked);
  if (!unlocked) {
    $("#access-token").focus();
  }
}

function createText(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value;
  return element;
}

function formatMemory(memoryMb) {
  const value = Number(memoryMb);
  if (!Number.isFinite(value)) return "—";
  return `${Math.max(0, Math.round(value / 1024)).toLocaleString("ko-KR")} GB`;
}

function formatGpuLabel(gpuType) {
  const normalized = String(gpuType || "").toLowerCase();
  const labels = {
    rtx3090: "RTX 3090",
    a10: "A10",
    a6000: "A6000",
    a6000ada: "A6000 Ada",
    h200: "H200",
  };
  return labels[normalized] || normalized.toUpperCase() || "GPU";
}

function nodeAcceptsJobs(node) {
  return ["idle", "mix", "alloc"].includes(node.state);
}

function requestLimitsForNode(node) {
  const safeNumber = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 0;
  };
  return {
    max_gpus: Math.min(safeNumber(node.gpus), safeNumber(node.free_gpus)),
    max_cpus: Math.min(safeNumber(node.cpus), safeNumber(node.cpu_idle)),
    max_memory_gb: Math.floor(
      Math.min(safeNumber(node.memory_mb), safeNumber(node.free_memory_mb)) / 1024,
    ),
  };
}

function waitingLimitsForNode(node) {
  const safeNumber = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 0;
  };
  return {
    max_gpus: safeNumber(node.gpus),
    max_cpus: safeNumber(node.cpus),
    max_memory_gb: Math.floor((safeNumber(node.memory_mb) / 1024) * 0.95),
  };
}

function nodeHasRequestCapacity(node) {
  const limits = requestLimitsForNode(node);
  return (
    nodeAcceptsJobs(node) &&
    limits.max_gpus >= 1 &&
    limits.max_cpus >= 1 &&
    limits.max_memory_gb >= 1
  );
}

function nodeCanQueue(node) {
  const limits = waitingLimitsForNode(node);
  return (
    nodeAcceptsJobs(node) &&
    limits.max_gpus >= 1 &&
    limits.max_cpus >= 1 &&
    limits.max_memory_gb >= 1
  );
}

function nodeCapacityMessage(node) {
  const limits = requestLimitsForNode(node);
  if (!nodeCanQueue(node)) return "새 요청 불가";
  if (!nodeHasRequestCapacity(node)) return "현재 여유 부족 · 대기 가능";
  return `${limits.max_gpus}/${node.gpus} GPU · ${limits.max_cpus}/${node.cpus} CPU 바로 사용`;
}

function isNodeSelected(nodeName) {
  return state.selectedNodes.some((detail) => detail.node.name === nodeName);
}

function selectionIsCompatible(node) {
  if (!state.selectedNodes.length) return true;
  const reference = state.selectedNodes[0];
  return (
    node.partition === reference.node.partition &&
    String(node.gpu_type || "").toLowerCase() ===
      String(reference.gpu_type || reference.node.gpu_type || "").toLowerCase()
  );
}

function combinedRequestLimits(mode = state.requestMode) {
  if (!state.selectedNodes.length) {
    return {
      max_gpus: 0,
      max_cpus: 0,
      max_memory_gb: 0,
      free_gpus: 0,
      free_cpus: 0,
      free_memory_gb: 0,
    };
  }
  const waiting = mode === "wait";
  const availableLimits = state.selectedNodes.map(
    (detail) => detail.request_limits || requestLimitsForNode(detail.node),
  );
  const selectedLimits = waiting
    ? state.selectedNodes.map(
        (detail) => detail.wait_limits || waitingLimitsForNode(detail.node),
      )
    : availableLimits;
  return {
    max_gpus: Math.min(...selectedLimits.map((limits) => limits.max_gpus)),
    max_cpus: Math.min(...selectedLimits.map((limits) => limits.max_cpus)),
    max_memory_gb: Math.min(
      ...selectedLimits.map((limits) => limits.max_memory_gb),
    ),
    free_gpus: availableLimits.reduce((sum, limits) => sum + limits.max_gpus, 0),
    free_cpus: availableLimits.reduce((sum, limits) => sum + limits.max_cpus, 0),
    free_memory_gb: availableLimits.reduce(
      (sum, limits) => sum + limits.max_memory_gb,
      0,
    ),
  };
}

function selectionSupportsMode(mode) {
  if (!state.selectedNodes.length) return false;
  const limits = combinedRequestLimits(mode);
  return (
    state.selectedNodes.every((detail) => nodeAcceptsJobs(detail.node)) &&
    limits.max_gpus >= 1 &&
    limits.max_cpus >= 1 &&
    limits.max_memory_gb >= 1
  );
}

function updateResourceAvailability() {
  const panel = $("#resource-availability");
  const resources = [
    {
      key: "gpu",
      unit: "개",
      input: "#gpu-count",
      hint: "#gpu-limit-hint",
    },
    {
      key: "cpu",
      unit: "코어",
      input: "#cpus",
      hint: "#cpu-limit-hint",
    },
    {
      key: "memory",
      unit: "GB",
      input: "#memory",
      hint: "#memory-limit-hint",
    },
  ];

  if (!state.selectedNodes.length) {
    panel.className = "resource-availability empty";
    $("#resource-availability-title").textContent = "선택 가능 한도";
    $("#resource-availability-mode").textContent = "노드 선택 필요";
    $("#resource-limit-caption").textContent = "노드당 입력";
    resources.forEach(({ key, hint }) => {
      $(`#resource-${key}-limit`).textContent = "—";
      $(`#resource-${key}-remaining`).textContent = "노드를 선택하세요";
      $(hint).textContent = "최대 —";
      const meter = $(`#resource-${key}-meter`);
      meter.style.width = "0%";
      meter.parentElement.setAttribute("aria-valuemin", "0");
      meter.parentElement.setAttribute("aria-valuemax", "0");
      meter.parentElement.setAttribute("aria-valuenow", "0");
      meter.parentElement.setAttribute("aria-label", `${key} 요청 한도`);
    });
    return;
  }

  const nodeCount = state.selectedNodes.length;
  const waiting = state.requestMode === "wait";
  const limits = combinedRequestLimits(state.requestMode);
  const values = {
    gpu: {
      limit: limits.max_gpus,
      free: limits.free_gpus,
    },
    cpu: {
      limit: limits.max_cpus,
      free: limits.free_cpus,
    },
    memory: {
      limit: limits.max_memory_gb,
      free: limits.free_memory_gb,
    },
  };

  panel.className = `resource-availability${waiting ? " waiting" : ""}`;
  $("#resource-availability-title").textContent = waiting
    ? "대기 요청 선택 한도"
    : "지금 선택 가능한 자원";
  $("#resource-availability-mode").textContent =
    `${nodeCount} nodes · ${waiting ? "노드 수용량 기준" : "현재 여유 기준"}`;
  $("#resource-limit-caption").textContent = waiting
    ? `${nodeCount}개 노드 · 노드당 입력`
    : `${nodeCount}개 노드 · 현재 잔여량 적용`;

  resources.forEach(({ key, unit, input, hint }) => {
    const { limit, free } = values[key];
    const requestedValue = Number($(input).value);
    const requested = Number.isFinite(requestedValue)
      ? Math.max(0, requestedValue)
      : 0;
    const totalLimit = limit * nodeCount;
    const totalRequested = requested * nodeCount;
    const remaining = Math.max(0, free - totalRequested);
    const percent =
      totalLimit > 0 ? Math.min(100, (totalRequested / totalLimit) * 100) : 0;
    const article = document.querySelector(`[data-resource-availability="${key}"]`);
    const meter = $(`#resource-${key}-meter`);

    $(`#resource-${key}-limit`).textContent =
      `${limit.toLocaleString("ko-KR")} ${unit}/node`;
    $(`#resource-${key}-remaining`).textContent = waiting
      ? `총 ${totalRequested.toLocaleString("ko-KR")} 요청 · 현재 여유 ${free.toLocaleString("ko-KR")}`
      : `총 ${totalRequested.toLocaleString("ko-KR")} 요청 · 이후 ${remaining.toLocaleString("ko-KR")} 남음`;
    $(hint).textContent = `최대 ${limit.toLocaleString("ko-KR")}/node`;
    article.classList.toggle("fully-requested", requested >= limit && limit > 0);
    meter.style.width = `${percent}%`;
    meter.parentElement.setAttribute("aria-valuemin", "0");
    meter.parentElement.setAttribute("aria-valuemax", String(totalLimit));
    meter.parentElement.setAttribute(
      "aria-valuenow",
      String(Math.min(totalRequested, totalLimit)),
    );
    meter.parentElement.setAttribute(
      "aria-label",
      `${key} 총 ${totalRequested}${unit} 요청, 최대 ${totalLimit}${unit}`,
    );
  });
}

function visibleNodes(nodes) {
  const query = state.nodeSearch.trim().toLowerCase();
  const filtered = nodes.filter((node) => {
      const selected = isNodeSelected(node.name);
      if (state.nodePartition && node.partition !== state.nodePartition) return selected;
      if (query && !`${node.name} ${node.partition} ${node.gpu_type}`.toLowerCase().includes(query)) {
        return selected;
      }
      if (state.nodeMode === "available" && !nodeHasRequestCapacity(node)) {
        return selected;
      }
      if (
        state.nodeMode === "waiting" &&
        !(nodeCanQueue(node) && !nodeHasRequestCapacity(node))
      ) {
        return selected;
      }
      return true;
    });
  const byNodeName = (left, right) =>
    left.name.localeCompare(right.name, undefined, { numeric: true });
  if (state.nodeSort === "available") {
    return filtered.sort(
      (left, right) =>
        right.free_gpus - left.free_gpus ||
        right.cpu_idle - left.cpu_idle ||
        right.free_memory_mb - left.free_memory_mb ||
        byNodeName(left, right),
    );
  }
  if (state.nodeSort === "partition") {
    return filtered.sort(
      (left, right) =>
        left.partition.localeCompare(right.partition, undefined, { numeric: true }) ||
        byNodeName(left, right),
    );
  }
  return filtered.sort(byNodeName);
}

function renderNodes(nodes) {
  const container = $("#node-list");
  container.replaceChildren();
  const filteredNodes = visibleNodes(nodes);
  if (!filteredNodes.length) {
    container.append(createText("div", "empty-state", "표시할 노드가 없습니다."));
    return;
  }
  filteredNodes.forEach((node) => {
    const gpuText = formatGpuLabel(node.gpu_type);
    const card = createText(
      "button",
      `node-card ${node.gpu_type === "h200" ? "h200-node" : ""} ` +
        `${isNodeSelected(node.name) ? "selected-node-card" : ""}`,
      "",
    );
    card.type = "button";
    card.dataset.node = node.name;
    card.setAttribute(
      "aria-label",
      `${node.name} 상세 보기, ${gpuText} ${node.gpus}개, 상태 ${node.state}`,
    );
    const header = createText("div", "node-header", "");
    const identity = createText("div", "node-identity", "");
    identity.append(
      createText("span", "node-name", node.name),
      createText("span", "node-partition", node.partition),
    );
    header.append(
      identity,
      createText("span", `state-badge ${node.state}`, node.state),
    );
    const allocated = node.cpu_allocated || 0;
    const idle = node.cpu_idle || 0;
    const utilization = node.cpus ? Math.round((allocated / node.cpus) * 100) : 0;
    const level = Math.min(100, Math.ceil(utilization / 10) * 10);
    const specGrid = createText("div", "node-spec-grid", "");
    const gpuSpec = createText("div", "node-spec-item", "");
    gpuSpec.append(
      createText("span", "", "GPU"),
      createText("strong", "", `${gpuText} × ${node.gpus}`),
    );
    const cpuSpec = createText("div", "node-spec-item", "");
    cpuSpec.append(
      createText("span", "", "CPU"),
      createText("strong", "", `${node.cpus} cores`),
    );
    const memorySpec = createText("div", "node-spec-item memory-spec", "");
    memorySpec.append(
      createText("span", "", "MEM FREE"),
      createText(
        "strong",
        "",
        `${formatMemory(node.free_memory_mb)} / ${formatMemory(node.memory_mb)}`,
      ),
    );
    specGrid.append(gpuSpec, cpuSpec, memorySpec);

    const meter = createText("div", "utilization", "");
    const meterLabel = createText("div", "utilization-label", "");
    meterLabel.append(
      createText("span", "", "CPU utilization"),
      createText("strong", "", `${utilization}%`),
    );
    const track = createText("div", "utilization-track", "");
    track.append(createText("span", `utilization-fill level-${level}`, ""));
    meter.append(meterLabel, track);
    const meta = createText("span", "node-meta", `${allocated} allocated · ${idle} idle`);
    card.append(header, specGrid, meter, meta);
    container.append(card);
  });
}

function renderRequestNodes(nodes) {
  const container = $("#request-node-list");
  container.replaceChildren();
  const filteredNodes = visibleNodes(nodes);
  $("#visible-node-count").textContent = `${filteredNodes.length}/${nodes.length} nodes`;
  if (!filteredNodes.length) {
    container.append(createText("div", "request-node-loading", "선택할 수 있는 노드가 없습니다."));
    return;
  }

  filteredNodes.forEach((node) => {
    const gpuLabel = formatGpuLabel(node.gpu_type);
    const selected = isNodeSelected(node.name);
    const pending = state.pendingNode === node.name;
    const waitingOnly = nodeCanQueue(node) && !nodeHasRequestCapacity(node);
    const limits = waitingOnly
      ? waitingLimitsForNode(node)
      : requestLimitsForNode(node);
    const unavailable = !nodeCanQueue(node);
    const incompatible = !selected && !selectionIsCompatible(node);
    const card = createText(
      "button",
      `request-node-choice ${node.gpu_type === "h200" ? "h200-choice" : ""} ` +
        `${selected ? "selected" : ""} ${pending ? "loading" : ""} ` +
        `${incompatible ? "incompatible" : ""}`,
      "",
    );
    card.type = "button";
    card.dataset.requestNode = node.name;
    card.setAttribute("role", "checkbox");
    card.setAttribute("aria-checked", String(selected));
    card.setAttribute("aria-busy", String(pending));
    card.disabled = unavailable || pending || incompatible;
    card.setAttribute(
      "aria-label",
      `${node.name}, ${node.partition} 파티션, ${gpuLabel} ${node.gpus}개, ` +
        `${limits.max_gpus}개 사용 가능, CPU ${limits.max_cpus}코어 사용 가능, ` +
        `요청 가능 메모리 ${limits.max_memory_gb} GB` +
        `${incompatible ? ", 현재 선택과 파티션 또는 GPU 종류가 달라 선택 불가" : ""}`,
    );

    const top = createText("div", "request-node-top", "");
    const identity = createText("div", "request-node-identity", "");
    identity.append(
      createText("strong", "", node.name),
      createText("span", "", node.partition),
    );
    top.append(
      identity,
      createText(
        "span",
        "request-node-mark",
        pending ? "…" : selected ? "✓" : "",
      ),
    );

    const gpu = createText("div", "request-node-gpu", "");
    gpu.append(
      createText("span", "", "GPU"),
      createText("strong", "", `${gpuLabel} × ${node.gpus}`),
    );

    const memory = createText("div", "request-node-memory", "");
    memory.append(
      createText("span", "", "실시간 여유 메모리"),
      createText("strong", "", formatMemory(node.free_memory_mb)),
    );

    const availability = createText(
      "div",
      `request-node-availability ${
        unavailable ? "unavailable" : waitingOnly ? "waiting" : ""
      }`,
      nodeCapacityMessage(node),
    );

    const footer = createText("div", "request-node-footer", "");
    footer.append(
      createText("span", "", `MEM ${limits.max_memory_gb} GB 요청 가능`),
      createText("span", `state-badge ${node.state}`, node.state),
    );
    card.append(top, gpu, memory, availability, footer);
    container.append(card);
  });
}

function renderNodeDetail(data) {
  const {
    node,
    summary,
    request_limits: limits,
    wait_limits: providedWaitLimits,
    gpu_slots: gpuSlots,
    jobs,
  } = data;
  const waitLimits = providedWaitLimits || waitingLimitsForNode(node);
  state.currentNodeDetail = data;
  const requestButton = $("#node-request-button");
  const selected = isNodeSelected(node.name);
  const compatible = selectionIsCompatible(node);
  const requestable =
    nodeAcceptsJobs(node) &&
    limits.max_gpus >= 1 &&
    limits.max_cpus >= 1 &&
    limits.max_memory_gb >= 1;
  const queueable =
    nodeAcceptsJobs(node) &&
    waitLimits.max_gpus >= 1 &&
    waitLimits.max_cpus >= 1 &&
    waitLimits.max_memory_gb >= 1;
  requestButton.disabled = !selected && (!queueable || !compatible);
  requestButton.textContent = selected
    ? "선택 해제"
    : !compatible
      ? "선택 노드와 파티션·GPU 종류가 다름"
    : requestable
      ? "요청에 노드 추가"
      : queueable
        ? "대기 요청에 노드 추가"
        : "현재 요청 가능한 자원 없음";
  $("#node-detail-title").textContent = node.name;
  $("#node-detail-state").textContent = node.state;
  $("#node-detail-state").className = `state-badge ${node.state}`;
  $("#node-detail-subtitle").textContent =
    `${node.partition} partition · ${node.cpus} CPU · ` +
    `${Math.round(node.memory_mb / 1024)} GB memory`;
  $("#node-total-gpus").textContent = summary.total_gpus;
  $("#node-allocated-gpus").textContent = summary.allocated_gpus;
  $("#node-free-gpus").textContent = summary.free_gpus;
  $("#node-free-memory").textContent = formatMemory(node.free_memory_mb);
  $("#node-job-count").textContent = summary.job_count;

  const gpuList = $("#gpu-slot-list");
  gpuList.replaceChildren();
  gpuSlots.forEach((slot) => {
    const card = createText(
      "article",
      `gpu-slot ${slot.allocated ? "allocated" : "available"} ` +
        `${slot.type === "h200" ? "h200-slot" : ""}`,
      "",
    );
    const header = createText("div", "gpu-slot-header", "");
    const identity = createText("div", "gpu-slot-identity", "");
    identity.append(
      createText("span", "gpu-index-label", "GPU"),
      createText("strong", "gpu-index", String(slot.index)),
    );
    header.append(
      identity,
      createText(
        "span",
        "gpu-status",
        slot.allocated ? "ALLOCATED" : "AVAILABLE",
      ),
    );

    const model = createText(
      "span",
      "gpu-model",
      `NVIDIA ${formatGpuLabel(slot.type)}`,
    );
    card.append(header, model);

    if (slot.allocated && slot.job) {
      const owner = createText("div", "gpu-owner", "");
      owner.append(
        createText(
          "span",
          "user-avatar",
          slot.job.user.slice(0, 2).toUpperCase(),
        ),
        createText("strong", "gpu-owner-name", slot.job.user),
      );
      const job = createText("div", "gpu-job", "");
      job.append(
        createText("span", "gpu-job-name", slot.job.name),
        createText("code", "", `#${slot.job.id}`),
      );
      const runtime = createText(
        "span",
        "gpu-runtime",
        `Running ${slot.job.runtime || "—"}`,
      );
      card.append(owner, job, runtime);
    } else if (slot.allocated) {
      const allocated = createText("div", "gpu-available-copy allocated-copy", "");
      allocated.append(
        createText("strong", "", "사용 중"),
        createText("span", "", "Job 사용자 정보를 확인할 수 없습니다."),
      );
      card.append(allocated);
    } else {
      const available = createText("div", "gpu-available-copy", "");
      available.append(
        createText("strong", "", "사용 가능"),
        createText("span", "", "현재 예약된 Job이 없습니다."),
      );
      card.append(available);
    }
    gpuList.append(card);
  });

  const jobList = $("#node-job-list");
  jobList.replaceChildren();
  if (!jobs.length) {
    jobList.append(
      createText("div", "detail-empty", "이 노드에서 실행 중인 GPU Job이 없습니다."),
    );
  } else {
    jobs.forEach((job) => {
      const row = createText(
        "article",
        `node-job-row ${job.is_current_user ? "mine" : ""}`,
        "",
      );
      const owner = createText("div", "node-job-owner", "");
      owner.append(
        createText("span", "user-avatar small-avatar", job.user.slice(0, 2).toUpperCase()),
        createText("strong", "", job.user),
      );
      const identity = createText("div", "node-job-identity", "");
      identity.append(
        createText("strong", "", job.name),
        createText("span", "mono", `Job #${job.id}`),
      );
      const gpu = createText("div", "node-job-gpus", "");
      gpu.append(
        createText("strong", "", `${job.gpu_count} GPU`),
        createText(
          "span",
          "mono",
          job.gpu_indices.map((index) => `#${index}`).join(" · "),
        ),
      );
      const runtime = createText("div", "node-job-runtime", "");
      runtime.append(
        createText("strong", "", job.runtime || "—"),
        createText("span", "", job.time_limit === "UNLIMITED" ? "No time limit" : `Limit ${job.time_limit}`),
      );
      row.append(owner, identity, gpu, runtime);
      jobList.append(row);
    });
  }

  const date = new Date(data.updated_at);
  $("#node-detail-updated").textContent =
    `${date.toLocaleTimeString("ko-KR")} 기준`;
}

async function loadNodeDetail(nodeName, silent = false) {
  const refreshButton = $("#node-detail-refresh");
  refreshButton.disabled = true;
  refreshButton.textContent = "불러오는 중…";
  try {
    const data = await api(`/api/nodes/${encodeURIComponent(nodeName)}`);
    renderNodeDetail(data);
  } catch (error) {
    if (!silent) toast(error.message, "error");
    $("#gpu-slot-list").replaceChildren(
      createText("div", "detail-empty error-copy", error.message),
    );
    $("#node-job-list").replaceChildren();
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = "새로고침";
  }
}

function openNodeDetail(nodeName) {
  state.currentNode = nodeName;
  state.currentNodeDetail = null;
  $("#node-request-button").disabled = true;
  $("#node-request-button").textContent = "정보 확인 중…";
  $("#node-detail-title").textContent = nodeName;
  $("#node-detail-subtitle").textContent = "Slurm 할당 정보를 불러오는 중입니다.";
  $("#gpu-slot-list").replaceChildren(
    createText("div", "detail-loading", "GPU 정보를 불러오는 중입니다."),
  );
  $("#node-job-list").replaceChildren(
    createText("div", "detail-loading", "Job 정보를 불러오는 중입니다."),
  );
  const dialog = $("#node-dialog");
  if (!dialog.open) dialog.showModal();
  loadNodeDetail(nodeName);
}

function clearNodeSelection({ closeDialog = false, announce = true } = {}) {
  const previousNodes = state.selectedNodes.map((detail) => detail.node.name);
  state.selectedNode = null;
  state.selectedNodes = [];
  state.pendingNode = "";
  state.requestMode = "available";
  $("#node-name").value = "";
  $("#selected-node-target").className = "selected-node-target empty";
  $("#selected-node-icon").textContent = "?";
  $("#selected-node-kicker").textContent = "NO NODES SELECTED";
  $("#selected-node-name").textContent = "왼쪽에서 노드를 선택하세요";
  $("#selected-node-spec").textContent =
    "선택하면 사용 가능한 자원 한도가 적용됩니다.";
  $("#node-description").textContent =
    "노드의 파티션과 GPU 종류는 서버가 자동으로 확인합니다.";
  ["#gpu-count", "#cpus", "#memory"].forEach((selector) => {
    $(selector).removeAttribute("max");
  });
  document.querySelectorAll("[data-request-mode]").forEach((button) => {
    button.disabled = true;
    button.classList.remove("active");
  });
  const submitButton = $("#submit-allocation");
  submitButton.disabled = true;
  submitButton.replaceChildren(
    createText("span", "", "노드를 먼저 선택하세요"),
    createText("span", "", "↗"),
  );
  renderFilteredNodeViews();
  updatePreview();
  if (closeDialog && $("#node-dialog").open) $("#node-dialog").close();
  if (announce && previousNodes.length) {
    toast(`${previousNodes.join(", ")} 선택을 해제했습니다.`);
  }
}

function setRequestMode(mode, { announce = false } = {}) {
  if (!state.selectedNodes.length) return false;
  const availableEnabled = selectionSupportsMode("available");
  const waitEnabled = selectionSupportsMode("wait");
  let nextMode = mode === "wait" ? "wait" : "available";
  if (nextMode === "available" && !availableEnabled) nextMode = "wait";
  if (nextMode === "wait" && !waitEnabled) return false;

  state.requestMode = nextMode;
  const limits = combinedRequestLimits(nextMode);
  const nodeCount = state.selectedNodes.length;
  const nodeNames = state.selectedNodes.map((detail) => detail.node.name);
  document.querySelectorAll("[data-request-mode]").forEach((button) => {
    const buttonMode = button.dataset.requestMode;
    button.disabled =
      buttonMode === "available" ? !availableEnabled : !waitEnabled;
    button.classList.toggle("active", buttonMode === nextMode);
    button.setAttribute("aria-pressed", String(buttonMode === nextMode));
  });

  $("#gpu-count").max = limits.max_gpus;
  $("#cpus").max = limits.max_cpus;
  $("#memory").max = limits.max_memory_gb;
  $("#gpu-count").value = Math.max(
    1,
    Math.min(Number($("#gpu-count").value) || 1, limits.max_gpus),
  );
  $("#cpus").value = Math.max(
    1,
    Math.min(Number($("#cpus").value) || 1, limits.max_cpus),
  );
  $("#memory").value = Math.max(
    1,
    Math.min(Number($("#memory").value) || 1, limits.max_memory_gb),
  );

  if (nextMode === "wait") {
    $("#selected-node-spec").textContent = nodeNames.join(" · ");
    $("#node-description").textContent =
      `${nodeCount}개 노드에 동일한 노드당 자원을 대기 요청합니다. 모든 노드의 자원이 함께 확보될 때 시작됩니다.`;
  } else {
    $("#selected-node-spec").textContent = nodeNames.join(" · ");
    $("#node-description").textContent =
      `${nodeCount}개 노드 모두에서 지금 확보할 수 있는 공통 범위만 선택합니다. 제출 직전에 다시 확인합니다.`;
  }

  const submitButton = $("#submit-allocation");
  submitButton.disabled = false;
  submitButton.replaceChildren(
    createText(
      "span",
      "",
      nextMode === "wait"
        ? `${nodeCount}개 노드에 대기 Job 제출`
        : `${nodeCount}개 노드에 Job 제출`,
    ),
    createText("span", "", "↗"),
  );
  updatePreview();
  if (announce) {
    toast(
      nextMode === "wait"
        ? "대기 요청 모드로 전환했습니다."
        : "현재 여유 자원 모드로 전환했습니다.",
    );
  }
  return true;
}

function applySelectedNodes(
  details,
  { closeDialog = true, scroll = true, announce = true, mode = null } = {},
) {
  state.pendingNode = "";
  const uniqueDetails = details.filter(
    (detail, index, items) =>
      items.findIndex((item) => item.node.name === detail.node.name) === index,
  );
  if (!uniqueDetails.length) {
    clearNodeSelection({ closeDialog, announce: false });
    return true;
  }
  if (uniqueDetails.length > MAX_SELECTED_NODES) {
    toast(`요청 노드는 최대 ${MAX_SELECTED_NODES}개까지 선택할 수 있습니다.`, "error");
    return false;
  }
  const unavailable = uniqueDetails.find((detail) => !nodeCanQueue(detail.node));
  if (unavailable) {
    toast(`${unavailable.node.name}에는 현재 요청을 제출할 수 없습니다.`, "error");
    return false;
  }
  const reference = uniqueDetails[0];
  const compatible = uniqueDetails.every(
    (detail) =>
      detail.node.partition === reference.node.partition &&
      String(detail.gpu_type || detail.node.gpu_type || "").toLowerCase() ===
        String(reference.gpu_type || reference.node.gpu_type || "").toLowerCase(),
  );
  if (!compatible) {
    toast("같은 파티션과 같은 GPU 종류의 노드만 함께 요청할 수 있습니다.", "error");
    return false;
  }

  state.selectedNodes = uniqueDetails;
  state.selectedNode = uniqueDetails[0];
  const nodeNames = uniqueDetails.map((detail) => detail.node.name);
  const nodeCount = uniqueDetails.length;
  const gpuLabel = formatGpuLabel(reference.gpu_type || reference.node.gpu_type);
  $("#node-name").value = nodeNames.join(",");
  $("#selected-node-target").classList.remove("empty");
  $("#selected-node-target").classList.toggle(
    "h200-target",
    String(reference.gpu_type || reference.node.gpu_type).toLowerCase() === "h200",
  );
  $("#selected-node-icon").textContent =
    nodeCount === 1 ? reference.node.name.slice(-1) : `${nodeCount}N`;
  $("#selected-node-kicker").textContent =
    `${reference.node.partition.toUpperCase()} · ${gpuLabel}`;
  $("#selected-node-name").textContent =
    nodeCount === 1 ? reference.node.name : `${nodeCount}개 노드 선택`;
  $("#selected-node-spec").textContent = nodeNames.join(" · ");
  const nextMode =
    mode ||
    (uniqueDetails.every((detail) => nodeHasRequestCapacity(detail.node))
      ? state.requestMode
      : "wait");
  setRequestMode(nextMode);
  document.querySelectorAll(".node-card").forEach((card) => {
    card.classList.toggle("selected-node-card", nodeNames.includes(card.dataset.node));
  });
  renderRequestNodes(state.nodes);
  updatePreview();
  if (closeDialog && $("#node-dialog").open) $("#node-dialog").close();
  if (scroll) {
    $("#allocation-panel").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (announce) toast(`${nodeNames.join(", ")}을 요청 대상으로 선택했습니다.`);
  return true;
}

function selectNodeForAllocation(
  detail,
  { closeDialog = true, scroll = true, announce = true, mode = null } = {},
) {
  const existingIndex = state.selectedNodes.findIndex(
    (item) => item.node.name === detail.node.name,
  );
  if (existingIndex >= 0) {
    const remaining = state.selectedNodes.filter((_, index) => index !== existingIndex);
    if (!remaining.length) {
      clearNodeSelection({ closeDialog, announce: false });
    } else {
      applySelectedNodes(remaining, {
        closeDialog,
        scroll,
        announce: false,
        mode: state.requestMode,
      });
    }
    if (announce) toast(`${detail.node.name} 선택을 해제했습니다.`);
    return true;
  }
  if (!nodeCanQueue(detail.node)) {
    toast(`${detail.node.name}에는 현재 제출하거나 대기할 수 없습니다.`, "error");
    return false;
  }
  if (!selectionIsCompatible(detail.node)) {
    toast("같은 파티션과 같은 GPU 종류의 노드만 함께 선택할 수 있습니다.", "error");
    return false;
  }
  if (state.selectedNodes.length >= MAX_SELECTED_NODES) {
    toast(`노드는 최대 ${MAX_SELECTED_NODES}개까지 선택할 수 있습니다.`, "error");
    return false;
  }
  const nextMode =
    mode ||
    (state.requestMode === "available" && !nodeHasRequestCapacity(detail.node)
      ? "wait"
      : state.requestMode);
  return applySelectedNodes([...state.selectedNodes, detail], {
    closeDialog,
    scroll,
    announce,
    mode: nextMode,
  });
}

function syncSelectedNodeWithOverview(nodes) {
  if (!state.selectedNodes.length) return;
  const previousNames = state.selectedNodes.map((detail) => detail.node.name);
  const updatedDetails = state.selectedNodes
    .map((detail) => {
      const latestNode = nodes.find((node) => node.name === detail.node.name);
      if (!latestNode || !nodeCanQueue(latestNode)) return null;
      return {
        ...detail,
        node: latestNode,
        gpu_type: latestNode.gpu_type,
        request_limits: requestLimitsForNode(latestNode),
        wait_limits: waitingLimitsForNode(latestNode),
        summary: {
          ...detail.summary,
          total_gpus: latestNode.gpus,
          allocated_gpus: latestNode.allocated_gpus,
          free_gpus: latestNode.free_gpus,
        },
      };
    })
    .filter(Boolean);
  if (!updatedDetails.length) {
    clearNodeSelection({ announce: false });
    toast("선택한 노드의 상태가 변경되어 선택을 해제했습니다.", "error");
    return;
  }
  const removed = previousNames.filter(
    (name) => !updatedDetails.some((detail) => detail.node.name === name),
  );
  const mustWait =
    state.requestMode === "available" &&
    updatedDetails.some((detail) => !nodeHasRequestCapacity(detail.node));
  applySelectedNodes(updatedDetails, {
    closeDialog: false,
    scroll: false,
    announce: false,
    mode: mustWait ? "wait" : state.requestMode,
  });
  if (removed.length) {
    toast(`${removed.join(", ")}의 상태가 변경되어 선택에서 제외했습니다.`, "error");
  } else if (mustWait) {
    toast("일부 노드의 현재 여유가 소진되어 대기 요청으로 전환했습니다.");
  }
}

async function selectRequestNode(nodeName) {
  state.pendingNode = nodeName;
  renderRequestNodes(state.nodes);
  try {
    const detail = await api(`/api/nodes/${encodeURIComponent(nodeName)}`);
    selectNodeForAllocation(detail, {
      closeDialog: false,
      scroll: false,
      announce: false,
    });
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.pendingNode = "";
    renderRequestNodes(state.nodes);
  }
}

function renderJobs(jobs) {
  const body = $("#jobs-body");
  body.replaceChildren();
  if (!jobs.length) {
    const row = document.createElement("tr");
    const cell = createText("td", "empty-state", "");
    cell.colSpan = 7;
    const empty = createText("div", "queue-empty", "");
    empty.append(
      createText("span", "queue-empty-mark", "0"),
      createText("strong", "", "큐가 비어 있습니다"),
      createText(
        "span",
        "",
        "현재 실행 또는 대기 중인 작업이 없습니다. 새 자원을 요청해 시작하세요.",
      ),
    );
    cell.append(empty);
    row.append(cell);
    body.append(row);
    return;
  }

  jobs.forEach((job) => {
    const row = document.createElement("tr");

    const identity = document.createElement("td");
    identity.append(
      createText("span", "job-name", job.name),
      createText("span", "job-sub mono", `#${job.id}`),
    );

    const stateCell = document.createElement("td");
    const normalized = job.state.toLowerCase();
    stateCell.append(createText("span", `job-state ${normalized}`, job.state));

    const partition = createText("td", "mono", job.partition);
    const resources = document.createElement("td");
    resources.append(
      createText("span", "", job.gres || "GPU 정보 없음"),
      createText("span", "job-sub", `${job.cpus} CPU · ${job.memory || "memory —"}`),
    );
    const location = document.createElement("td");
    location.append(
      createText("span", "", job.nodes || "—"),
      createText("span", "job-sub", job.reason || "—"),
    );
    const time = document.createElement("td");
    time.append(
      createText("span", "mono", job.elapsed || "—"),
      createText("span", "job-sub mono", `남음 ${job.remaining || "—"}`),
    );
    const action = document.createElement("td");
    if (job.portal_managed) {
      const button = createText("button", "danger-button", "취소");
      button.type = "button";
      button.dataset.cancelJob = job.id;
      button.dataset.jobName = job.name;
      action.append(button);
    } else {
      action.append(createText("span", "read-only", "조회만"));
    }

    row.append(identity, stateCell, partition, resources, location, time, action);
    body.append(row);
  });
}

function updateNodeFilterControls(nodes) {
  const partitions = Array.from(new Set(nodes.map((node) => node.partition))).sort(
    (left, right) => left.localeCompare(right, undefined, { numeric: true }),
  );
  const select = $("#node-partition-filter");
  const currentOptions = Array.from(select.options)
    .slice(1)
    .map((option) => option.value);
  if (currentOptions.join("|") !== partitions.join("|")) {
    select.replaceChildren(new Option("모든 GPU 파티션", ""));
    partitions.forEach((partition) => {
      const partitionNodes = nodes.filter((node) => node.partition === partition);
      const gpuTypes = Array.from(
        new Set(partitionNodes.map((node) => formatGpuLabel(node.gpu_type))),
      ).join(" · ");
      select.append(
        new Option(`${partition} · ${gpuTypes} · ${partitionNodes.length} nodes`, partition),
      );
    });
  }
  if (state.nodePartition && !partitions.includes(state.nodePartition)) {
    state.nodePartition = "";
  }
  select.value = state.nodePartition;
  document.querySelectorAll("[data-node-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.nodeMode === state.nodeMode);
  });
}

function renderFilteredNodeViews() {
  renderNodes(state.nodes);
  renderRequestNodes(state.nodes);
}

function updateMetrics(data) {
  $("#metric-nodes").textContent = data.summary.nodes;
  $("#metric-gpus").textContent = data.summary.total_gpus;
  $("#metric-jobs").textContent = data.summary.jobs;
  $("#metric-jobs-detail").textContent =
    `${data.summary.running} running · ${data.summary.pending} pending`;
  $("#metric-user").textContent = data.user;
  $("#footer-user").textContent = data.user;
  $("#sidebar-server").textContent = data.server;
  $("#sidebar-cluster").textContent = `${data.cluster} · Slurm cluster`;
  $("#page-cluster-label").textContent =
    `${data.server} · ${data.cluster}`.toUpperCase();
  $("#footer-server").textContent = data.server;
  $("#metric-gpu-types").textContent = Array.from(
    new Set(data.nodes.map((node) => formatGpuLabel(node.gpu_type))),
  ).join(" · ");
  document.title = `MIL Compute · ${data.server}`;
  const activeNodes = data.nodes.filter((node) =>
    ["idle", "mix", "alloc"].includes(node.state),
  ).length;
  $("#metric-nodes-detail").textContent = `${activeNodes}/${data.summary.nodes} nodes online`;
  const date = new Date(data.updated_at);
  $("#last-updated").textContent = `${date.toLocaleTimeString("ko-KR")} 갱신`;
}

async function loadOverview(silent = false) {
  try {
    const data = await api("/api/overview");
    state.nodes = data.nodes;
    syncSelectedNodeWithOverview(data.nodes);
    if (!state.nodeMode) {
      state.nodeMode = data.nodes.length > 20 ? "available" : "all";
    }
    updateNodeFilterControls(data.nodes);
    updateMetrics(data);
    renderFilteredNodeViews();
    renderJobs(data.jobs);
    setConnection(true, `${data.server} 연결됨`);
  } catch (error) {
    setConnection(false, "연결 오류");
    if (!silent) toast(error.message, "error");
  }
}

function updatePreview() {
  updateResourceAvailability();
  if (!state.selectedNodes.length) {
    $("#request-preview").textContent = "노드를 선택하세요";
    return;
  }
  const node = state.selectedNodes[0].node;
  const nodeCount = state.selectedNodes.length;
  const gpusPerNode = Number($("#gpu-count").value) || 0;
  const totalGpus = gpusPerNode * nodeCount;
  const timeLimit = $("#no-time-limit").checked ? "No limit" : `${$("#hours").value}h`;
  const requestMode = state.requestMode === "wait" ? "Wait queue" : "Available now";
  $("#request-preview").textContent =
    `${requestMode} / ${nodeCount} nodes / ${node.partition} / ` +
    `${gpusPerNode} GPU per node · ${totalGpus} GPU total / ` +
    `${$("#cpus").value} CPU per node / ${$("#memory").value} GB per node / ${timeLimit}`;
}

function updateTimeLimitMode() {
  const unlimited = $("#no-time-limit").checked;
  $("#hours").disabled = unlimited;
  $("#hours").required = !unlimited;
  $(".time-resource-field").classList.toggle("unlimited", unlimited);
  updatePreview();
}

async function authorize(token) {
  state.token = token.trim();
  const data = await api("/api/auth", { method: "POST" });
  state.csrf = data.csrf_token;
  sessionStorage.setItem("tgm-portal-token", state.token);
  setUnlocked(true);
  setConnection(true, "Slurm 연결됨");
  await loadOverview();
  window.clearInterval(state.timer);
  state.timer = window.setInterval(() => loadOverview(true), 10000);
}

$("#unlock-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#unlock-error").textContent = "";
  try {
    await authorize($("#access-token").value);
    $("#access-token").value = "";
  } catch (error) {
    state.token = "";
    $("#unlock-error").textContent = error.message;
  }
});

$("#refresh").addEventListener("click", () => loadOverview());
$("#focus-form").addEventListener("click", () => {
  $("#allocation-panel").scrollIntoView({ behavior: "smooth", block: "start" });
});

$(".filter-modes").addEventListener("click", (event) => {
  const button = event.target.closest("[data-node-mode]");
  if (!button) return;
  state.nodeMode = button.dataset.nodeMode;
  updateNodeFilterControls(state.nodes);
  renderFilteredNodeViews();
});

$("#node-partition-filter").addEventListener("change", (event) => {
  state.nodePartition = event.target.value;
  renderFilteredNodeViews();
});

$("#node-sort").addEventListener("change", (event) => {
  state.nodeSort = event.target.value;
  renderFilteredNodeViews();
});

$("#node-search").addEventListener("input", (event) => {
  state.nodeSearch = event.target.value;
  renderFilteredNodeViews();
});

$("#request-mode-switch").addEventListener("click", (event) => {
  const button = event.target.closest("[data-request-mode]");
  if (!button || button.disabled || !state.selectedNodes.length) return;
  setRequestMode(button.dataset.requestMode, { announce: true });
});

const navItems = Array.from(document.querySelectorAll(".nav-item"));
const navSections = navItems
  .map((item) => document.querySelector(item.getAttribute("href")))
  .filter(Boolean);

function updateActiveNavigation() {
  const current =
    navSections
      .filter((section) => section.getBoundingClientRect().top <= 130)
      .at(-1) || navSections[0];
  navItems.forEach((item) => {
    item.classList.toggle("active", item.getAttribute("href") === `#${current.id}`);
  });
}

$(".sidebar-nav").addEventListener("click", (event) => {
  const item = event.target.closest(".nav-item");
  if (!item) return;
  navItems.forEach((candidate) => candidate.classList.toggle("active", candidate === item));
});
window.addEventListener("scroll", updateActiveNavigation, { passive: true });

$("#allocation-form").addEventListener("input", updatePreview);

$("#no-time-limit").addEventListener("change", updateTimeLimitMode);

$("#request-node-list").addEventListener("click", (event) => {
  const card = event.target.closest("[data-request-node]");
  if (!card || card.disabled) return;
  selectRequestNode(card.dataset.requestNode);
});

$("#node-list").addEventListener("click", (event) => {
  const card = event.target.closest("[data-node]");
  if (!card) return;
  openNodeDetail(card.dataset.node);
});

$("#node-dialog-close").addEventListener("click", () => {
  $("#node-dialog").close();
  state.currentNode = "";
  state.currentNodeDetail = null;
});

$("#node-detail-refresh").addEventListener("click", () => {
  if (state.currentNode) loadNodeDetail(state.currentNode);
});

$("#node-request-button").addEventListener("click", () => {
  if (!state.currentNodeDetail) return;
  selectNodeForAllocation(state.currentNodeDetail);
});

$("#node-dialog").addEventListener("click", (event) => {
  if (event.target === $("#node-dialog")) {
    $("#node-dialog").close();
    state.currentNode = "";
  }
});

$("#node-dialog").addEventListener("close", () => {
  state.currentNode = "";
  state.currentNodeDetail = null;
});

$("#allocation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedNodes.length) {
    toast("요청할 노드를 먼저 선택하세요.", "error");
    $("#request-node-list").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const node = state.selectedNodes[0].node;
  const nodeNames = state.selectedNodes.map((detail) => detail.node.name);
  const nodeCount = nodeNames.length;
  const unlimited = $("#no-time-limit").checked;
  const request = {
    node_names: nodeNames,
    gpu_count: Number($("#gpu-count").value),
    cpus: Number($("#cpus").value),
    memory_gb: Number($("#memory").value),
    hours: unlimited ? 0 : Number($("#hours").value),
    wait_for_resources: state.requestMode === "wait",
  };
  const timeSummary = unlimited ? "시간 제한 없이" : `${request.hours}시간 동안`;
  const modeSummary =
    state.requestMode === "wait" ? "대기 요청으로" : "현재 여유 자원에서";
  const totalGpus = request.gpu_count * nodeCount;
  const summary =
    `${modeSummary} ${nodeNames.join(", ")} (${node.partition})에 노드당 ` +
    `${request.gpu_count} GPU, ${request.cpus} CPU, ${request.memory_gb} GB를 요청합니다. ` +
    `총 ${nodeCount}개 노드 · ${totalGpus} GPU · ${timeSummary}입니다. 계속할까요?`;
  if (!window.confirm(summary)) return;

  const button = $("#submit-allocation");
  button.disabled = true;
  button.textContent = "제출 중…";
  try {
    const result = await api("/api/allocations", {
      method: "POST",
      body: JSON.stringify(request),
    });
    toast(`Job #${result.job_id} 요청을 제출했습니다.`);
    clearNodeSelection({ announce: false });
    await loadOverview(true);
  } catch (error) {
    toast(error.message, "error");
    clearNodeSelection({ announce: false });
    await loadOverview(true);
  } finally {
    const hasSelection = state.selectedNodes.length > 0;
    const selectedCount = state.selectedNodes.length;
    button.disabled = !hasSelection;
    button.replaceChildren(
      createText(
        "span",
        "",
        hasSelection
          ? state.requestMode === "wait"
            ? `${selectedCount}개 노드에 대기 Job 제출`
            : `${selectedCount}개 노드에 Job 제출`
          : "노드를 먼저 선택하세요",
      ),
      createText("span", "", "↗"),
    );
  }
});

$("#jobs-body").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-cancel-job]");
  if (!button) return;
  const jobId = button.dataset.cancelJob;
  const name = button.dataset.jobName;
  if (!window.confirm(`${name} (#${jobId})을 취소하고 자원을 반납할까요?`)) return;
  button.disabled = true;
  try {
    await api(`/api/jobs/${jobId}/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    toast(`Job #${jobId} 취소 요청을 보냈습니다.`);
    await loadOverview(true);
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
  }
});

updateTimeLimitMode();

if (state.token) {
  authorize(state.token).catch(() => {
    sessionStorage.removeItem("tgm-portal-token");
    state.token = "";
    setUnlocked(false);
  });
} else {
  setUnlocked(false);
}
