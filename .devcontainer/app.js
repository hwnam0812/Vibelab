const state = {
  projectId: null,
  project: null,
  configuration: null,
  features: null,
  originalIdea: "",
  originalRequest: ""
};

const $ = (sel) => document.querySelector(sel);

function showToast(message, type = "info") {
  const el = $("#toast");
  el.textContent = message;
  el.style.borderColor = type === "error" ? "#7f1d1d" : type === "success" ? "#166534" : "#344257";
  el.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.classList.remove("show"), 3200);
}

function setTask(name, status = "done") {
  const el = document.querySelector(`[data-task="${name}"]`);
  if (!el) return;
  el.classList.remove("done", "running");
  if (status === "done") {
    el.classList.add("done");
    el.textContent = `✓ ${el.textContent.replace(/^[○⟳✓] /, "")}`;
  } else if (status === "running") {
    el.classList.add("running");
    el.textContent = `⟳ ${el.textContent.replace(/^[○⟳✓] /, "")}`;
  } else {
    el.textContent = `○ ${el.textContent.replace(/^[○⟳✓] /, "")}`;
  }
  $("#taskStatus").textContent = status === "running" ? "AI 작업 중..." : "작업 완료";
}

function resetTasks() {
  document.querySelectorAll("#progressList div").forEach((el) => {
    el.className = "";
    el.textContent = el.textContent.replace(/^[○⟳✓] /, "");
    el.textContent = `○ ${el.textContent}`;
  });
  $("#taskStatus").textContent = "대기 중";
}

function pretty(obj) {
  return JSON.stringify(obj ?? {}, null, 2);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

function setView(name) {
  document.querySelectorAll(".view").forEach((el) => el.classList.add("hidden"));
  $(`#view-${name}`).classList.remove("hidden");

  document.querySelectorAll(".step").forEach((el) => {
    el.classList.toggle("active", el.dataset.step === name);
  });

  if (name === "manage") renderState();
}

async function createProject() {
  const name = prompt("프로젝트 이름을 입력하세요.", "My Vibe App");
  if (!name) return;
  const project = await api("/api/projects", {
    method: "POST",
    body: JSON.stringify({name}),
  });
  state.projectId = project.project_id;
  state.project = project;
  state.configuration = null;
  state.features = null;
  state.originalIdea = "";
  state.originalRequest = "";
  resetTasks();
  renderProject();
  showToast("새 프로젝트가 생성되었습니다.", "success");
  setView("prompt");
}

function renderProject() {
  $("#projectName").textContent = state.project?.project_name || "새 프로젝트";
  $("#projectId").textContent = state.projectId ? `ID: ${state.projectId}` : "";
  renderFiles(state.project?.files || []);
}

async function ensureProject() {
  if (state.projectId) return;
  const project = await api("/api/projects", {
    method: "POST",
    body: JSON.stringify({name: "Vibe Project"}),
  });
  state.projectId = project.project_id;
  state.project = project;
  renderProject();
}

async function healthCheck() {
  try {
    const result = await api("/api/health");
    $("#connection").textContent = result.aiConfigured
      ? `AI 연결 · ${result.model}`
      : "API Key 미설정";
    $("#connection").classList.toggle("success", !!result.aiConfigured);
  } catch {
    $("#connection").textContent = "Backend 연결 실패";
  }
}

function renderFiles(files) {
  const container = $("#fileList");
  container.innerHTML = "";
  if (!files?.length) {
    container.innerHTML = `<div class="muted">아직 파일이 없습니다.</div>`;
    return;
  }
  for (const file of files) {
    const item = document.createElement("div");
    item.className = "file-item mono";
    item.textContent = file.path;
    item.addEventListener("click", () => loadFile(file.path));
    container.appendChild(item);
  }

  const select = $("#fileSelect");
  select.innerHTML = files.map(f => `<option value="${escapeHtml(f.path)}">${escapeHtml(f.path)}</option>`).join("");
  if (files[0]) loadFile(files[0].path);
}

async function loadFile(path) {
  if (!path || !state.projectId) return;
  try {
    const result = await api(`/api/projects/${state.projectId}/file?path=${encodeURIComponent(path)}`);
    $("#fileContent").textContent = result.content;
    $("#fileSelect").value = path;
  } catch (e) {
    showToast(e.message, "error");
  }
}

function escapeHtml(text = "") {
  return String(text).replace(/[&<>"']/g, (m) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[m]));
}

async function refreshProject() {
  if (!state.projectId) return;
  state.project = await api(`/api/projects/${state.projectId}`);
  renderProject();
}

$("#newProject").addEventListener("click", createProject);

document.querySelectorAll(".step").forEach(btn => {
  btn.addEventListener("click", () => setView(btn.dataset.step));
});

$("#analyzeIdea").addEventListener("click", async () => {
  const idea = $("#idea").value.trim();
  if (!idea) return showToast("아이디어를 입력해주세요.", "error");
  await ensureProject();

  setTask("analysis", "running");
  $("#agentBadge").textContent = "WORKING";

  try {
    state.originalIdea = idea;
    const result = await api("/api/analyze", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.projectId,
        idea,
      }),
    });

    state.configuration = result;
    $("#configurationJson").textContent = pretty(result);
    $("#configurationResult").classList.remove("hidden");
    setTask("analysis", "done");
    setTask("design", "done");
    await refreshProject();
    showToast("앱 구성 설계가 완료되었습니다.", "success");
  } catch (e) {
    showToast(e.message, "error");
  } finally {
    $("#agentBadge").textContent = "IDLE";
  }
});

$("#goFeatures").addEventListener("click", () => {
  $("#featuresConfig").textContent = pretty(state.configuration);
  setView("features");
});

$("#refreshConfig").addEventListener("click", () => {
  $("#featuresConfig").textContent = pretty(state.configuration);
});

$("#designFeatures").addEventListener("click", async () => {
  if (!state.configuration) return showToast("먼저 앱 구성을 설계해주세요.", "error");
  const note = $("#featureNote").value.trim();
  setTask("design", "running");

  try {
    const config = {
      ...state.configuration,
      additionalRequirements: note ? [note] : [],
    };
    const result = await api("/api/features", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.projectId,
        configuration: config,
      }),
    });
    state.features = result;
    $("#featureJson").textContent = pretty(result);
    $("#featureResult").classList.remove("hidden");
    setTask("design", "done");
    showToast("앱 기능 명세가 완료되었습니다.", "success");
    await refreshProject();
  } catch (e) {
    showToast(e.message, "error");
  }
});

$("#goCoding").addEventListener("click", () => {
  $("#codingRequest").value = state.originalIdea || "설계된 앱을 실제로 구현해줘.";
  setView("coding");
});

$("#runCoding").addEventListener("click", async () => {
  const request = $("#codingRequest").value.trim();
  if (!request) return showToast("코딩 요청을 입력해주세요.", "error");
  await ensureProject();

  state.originalRequest = request;
  setTask("project", "running");
  setTask("implementation", "running");
  $("#agentBadge").textContent = "WORKING";
  $("#agentLog").textContent = "요구사항 분석 → 기존 프로젝트 분석 → 계획 → 구현 → 테스트\n";

  try {
    const result = await api("/api/code", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.projectId,
        request,
      }),
    });

    $("#agentLog").textContent += pretty(result) + "\n";
    if (result.status === "structure_change_required") {
      showToast("구조 변경 승인이 필요한 작업입니다.", "error");
      return;
    }

    setTask("project", "done");
    setTask("implementation", "done");
    setTask("test", result.tests?.passed ? "done" : "running");
    await refreshProject();
    showToast("코드 생성과 1차 테스트가 완료되었습니다.", "success");
  } catch (e) {
    $("#agentLog").textContent += `ERROR: ${e.message}\n`;
    setTask("implementation", "running");
    showToast(e.message, "error");
  } finally {
    $("#agentBadge").textContent = "IDLE";
  }
});

$("#fileSelect").addEventListener("change", (e) => loadFile(e.target.value));

$("#runTests").addEventListener("click", async () => {
  if (!state.projectId) return showToast("프로젝트가 없습니다.", "error");
  setTask("test", "running");
  try {
    const result = await api(`/api/test?project_id=${encodeURIComponent(state.projectId)}`, {
      method: "POST",
    });
    $("#testResult").textContent = pretty(result);
    setTask("test", result.passed ? "done" : "running");
    showToast(result.passed ? "테스트 통과" : "일부 테스트 실패", result.passed ? "success" : "error");
  } catch (e) {
    showToast(e.message, "error");
  }
});

$("#runReview").addEventListener("click", async () => {
  if (!state.projectId) return showToast("프로젝트가 없습니다.", "error");
  setTask("verify", "running");
  try {
    const original = state.originalIdea || $("#codingRequest").value || "현재 프로젝트를 검수해주세요.";
    const result = await api(
      `/api/review?project_id=${encodeURIComponent(state.projectId)}&original_request=${encodeURIComponent(original)}`,
      {method: "POST"}
    );
    $("#reviewScore").textContent = `${result.score ?? "-"} / 100`;
    $("#reviewResult").textContent = pretty(result);
    $("#repairIssue").value = (result.recommendedFixes || []).join("\n");
    setTask("verify", "done");
    showToast("AI 검수가 완료되었습니다.", "success");
  } catch (e) {
    showToast(e.message, "error");
  }
});

$("#repair").addEventListener("click", async () => {
  const issue = $("#repairIssue").value.trim();
  if (!issue) return showToast("수정할 문제를 입력해주세요.", "error");
  setTask("repair", "running");

  try {
    const result = await api("/api/repair", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.projectId,
        original_request: state.originalRequest || state.originalIdea,
        issue,
      }),
    });

    $("#testResult").textContent = pretty(result);
    setTask("repair", "done");
    setTask("test", result.tests?.passed ? "done" : "running");
    setTask("verify", "done");
    await refreshProject();
    showToast("수정 후 재검증이 완료되었습니다.", "success");
  } catch (e) {
    showToast(e.message, "error");
  }
});

async function renderState() {
  if (!state.projectId) return;
  try {
    const project = await api(`/api/projects/${state.projectId}`);
    state.project = project;
    renderProject();
    $("#stateResult").textContent = pretty(project);
  } catch (e) {
    showToast(e.message, "error");
  }
}

$("#refreshState").addEventListener("click", renderState);

(async function boot() {
  await healthCheck();
  await ensureProject();
  setView("prompt");
})();
