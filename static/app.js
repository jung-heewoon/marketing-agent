const state = {
  videos: [],
  selectedId: null,
  risingOnly: false,
  draft: null,
  limits: { min: 40, max: 120, brand: "운명원" },
  profiles: [],
  today: 0,
  postedVideoIds: [],
  dailyMax: 10,
};

let watchToken = 0;

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : "요청에 실패했습니다.");
  }
  return data;
}

function selectedProfile() {
  const picked = document.querySelector("input[name=profile]:checked");
  return picked ? picked.value : "youtube1";
}

function setHealth(ok, detail, model) {
  const node = $("health");
  node.textContent = model ? `${detail} · ${model}` : detail;
  node.classList.toggle("ok", ok);
  node.classList.toggle("bad", !ok);
}

function profileLabel(id) {
  const found = state.profiles.find((item) => item.id === id);
  return found ? found.label : id;
}

function applyLimits(data) {
  if (data.profiles) state.profiles = data.profiles;
  if (typeof data.today === "number") state.today = data.today;
  if (data.posted_video_ids) state.postedVideoIds = data.posted_video_ids;
  if (data.daily_max) state.dailyMax = data.daily_max;
  renderQuota();
}

function renderQuota() {
  const max = state.dailyMax || 10;
  $("quota").textContent = `오늘 ${state.today || 0} / ${max}`;
}

function blockReason() {
  if (!state.selectedId) return "";
  if ((state.postedVideoIds || []).includes(state.selectedId)) {
    return "이 영상에는 이미 댓글을 남겼습니다. 영상당 1개만 게시할 수 있습니다.";
  }
  if ((state.today || 0) >= (state.dailyMax || 10)) {
    return `하루 게시 상한 ${state.dailyMax}개에 도달했습니다.`;
  }
  return "";
}

function syncBlockNote() {
  const reason = blockReason();
  const node = $("post-status");
  const current = node.textContent || "";
  const showingBlock = current.includes("이미 댓글") || current.includes("하루 게시 상한");
  if (reason) node.textContent = reason;
  else if (showingBlock) node.textContent = "";
}

function setStep(step) {
  const order = ["프로필 여는 중", "입력 중", "게시 완료 · 댓글 보기"];
  const index = order.indexOf(step);
  document.querySelectorAll("#steps li").forEach((item, itemIndex) => {
    item.classList.toggle("active", itemIndex === index);
    item.classList.toggle("done", index === order.length - 1 || (index > itemIndex && index !== -1));
  });
  if (step) $("post-status").textContent = step;
}

function renderVideos() {
  const root = $("videos");
  if (!state.videos.length) {
    root.innerHTML = `<p class="note">${state.risingOnly ? "조건에 맞는 영상이 없습니다. 조회수 1,000~5,000이고 시간당 150 이상인 영상만 표시합니다." : "영상 불러오기를 누르면 여기 목록이 채워집니다."}</p>`;
    return;
  }
  root.innerHTML = state.videos.map((video) => {
    const postedNames = (video.posted_profiles || []).map((id) => profileLabel(id));
    const badges = [
      postedNames.length ? `<span class="badge">${esc(postedNames.join(", "))} 게시됨</span>` : "",
      video.rising ? `<span class="badge">뜨기 직전 ${esc(video.view_count)}회 · 시간당 ${esc(video.views_per_hour)}</span>` : "",
    ].join("");
    return `<button type="button" class="card${video.video_id === state.selectedId ? " active" : ""}" data-id="${esc(video.video_id)}">
      <img src="${esc(video.thumbnail)}" alt="" />
      <span>
        <b>${esc(video.title)}</b>
        <span class="meta">${esc(video.channel_name)} · ${esc(video.published_label)}</span>
        ${badges}
      </span>
    </button>`;
  }).join("");
  root.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("click", () => selectVideo(card.dataset.id, state.risingOnly));
  });
}

function renderPicked() {
  const video = state.videos.find((item) => item.video_id === state.selectedId);
  const node = $("picked");
  if (!video) {
    node.className = "picked empty";
    node.textContent = "왼쪽에서 영상을 고르세요.";
    return;
  }
  node.className = "picked";
  node.innerHTML = `<b>${esc(video.title)}</b><span class="meta">${esc(video.channel_name)} · ${esc(video.published_label)}</span>`;
}

function localIssues(comment) {
  const text = comment.trim();
  const issues = [];
  if (text.length < state.limits.min || text.length > state.limits.max) {
    issues.push(`${state.limits.min}~${state.limits.max}자`);
  }
  if (!text.includes(state.limits.brand)) issues.push(state.limits.brand);
  if (/[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(text)) issues.push("한자");
  if (/[·・•∙･]/.test(text) || /연애\s*(?:랑|와|과|하고|이랑|및|,)?\s*인연|인연\s*(?:랑|와|과|하고|이랑|및|,)?\s*연애/.test(text)) {
    issues.push("가운데점");
  }
  if (/내\s*짝.{0,12}(?:있는지|있나|알고\s*싶|보고\s*싶)|짝(?:이나|이)\s*있는지/.test(text)) issues.push("반복 결말");
  if (/소문|보이더라|보이네요|고민\s*중|말에\s*찔/.test(text)) issues.push("뜬금없는 브랜드");
  if (/다는\s*말|셋째|세\s*호흡|말투\s*참고|같은\s*끝|말(?:이다)?[.!]?\s*$/.test(text)) issues.push("설명문");
  return issues;
}

function refreshPostButton() {
  const comment = $("comment").value;
  const issues = localIssues(comment);
  const video = state.videos.find((item) => item.video_id === state.selectedId);
  const blocked = state.risingOnly || !video || !state.draft || issues.length > 0 || heldDraft(comment);
  $("post").disabled = blocked;
  $("make-draft").disabled = !state.selectedId;
  $("redo").disabled = !state.selectedId || !state.draft;
  const count = $("chars");
  count.textContent = `${comment.trim().length}자`;
  count.classList.toggle("bad", issues.length > 0 && comment.trim().length > 0);
  if (issues.includes("한자") || issues.includes("가운데점") || issues.includes("반복 결말") || issues.includes("뜬금없는 브랜드") || issues.includes("설명문")) {
    const notes = [];
    if (issues.includes("한자")) notes.push("한자를 한글로 바꾸세요.");
    if (issues.includes("가운데점")) notes.push("가운데점 없이 연애사주나 인연사주 중 하나만 쓰세요.");
    if (issues.includes("반복 결말")) notes.push("같은 결말은 이번 영상 고민으로 바꾸세요.");
    if (issues.includes("뜬금없는 브랜드")) notes.push("소문이나 고민 중 같은 말은 빼고, 운명원은 문장 안에 남기세요.");
    if (issues.includes("설명문")) notes.push("끝 문장이 설명문이다. 시청자가 입 밖으로 하는 말로 다시 써라.");
    $("draft-status").textContent = notes.join(" ");
  }
}

function heldDraft(comment) {
  const draft = state.draft;
  if (!draft) return false;
  const warnings = draft.warnings || [];
  if (!warnings.some((item) => item.includes("게시 불가") || item.includes("게시 보류") || item.includes("검토 필요"))) return false;
  return comment.trim() === (draft.comment || "").trim();
}

function fillDraft(draft) {
  state.draft = draft;
  $("concern").textContent = draft.concern ? `고민: ${draft.concern}` : "";
  $("comment").value = draft.comment || "";
  $("evidence").textContent = draft.evidence_label || (draft.evidence ? `문자열 근거: ${draft.evidence}` : "");
  const warnings = draft.warnings || [];
  const note = draft.pick_note || "";
  if (warnings.length) {
    $("draft-status").textContent = [note, ...warnings].filter(Boolean).join(" ");
  } else if ((draft.comment || "").trim()) {
    $("draft-status").textContent = note || "댓글 초안입니다. 게시할 때 이 문장을 다시 검사합니다.";
  } else {
    $("draft-status").textContent = note || "댓글 줄을 읽지 못했습니다. 다시 만들어 보세요.";
  }
  refreshPostButton();
}

let saveTimer = 0;

async function saveComment(comment) {
  if (!state.selectedId || !state.draft) return;
  const videoId = state.selectedId;
  const saved = await api("/api/drafts/comment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_id: videoId, comment }),
  });
  if (state.selectedId !== videoId || !state.draft) return;
  state.draft.comment = saved.comment;
  state.draft.evidence = saved.evidence || "";
  state.draft.evidence_label = saved.evidence_label || "";
  state.draft.candidates = saved.candidates || state.draft.candidates;
  state.draft.concern = saved.concern || state.draft.concern;
  $("evidence").textContent = saved.evidence_label || "";
}

async function watch(jobId, token) {
  while (token === watchToken) {
    const job = await api(`/api/jobs/${jobId}`);
    if (job.kind === "post" || job.kind === "login") setStep(job.step);
    if ((job.kind === "draft" || job.kind === "livewiki") && job.step) {
      $("draft-status").textContent = job.step;
    }
    if (job.status !== "running") return job;
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
  return null;
}

async function selectVideo(videoId, fromRising) {
  const token = ++watchToken;
  state.selectedId = videoId;
  state.risingOnly = fromRising;
  state.draft = null;
  renderVideos();
  renderPicked();
  $("comment").value = "";
  $("concern").textContent = "";
  $("evidence").textContent = "";
  $("draft-status").textContent = "초안 만들기를 누르면 고민 한 줄과 댓글 초안이 나옵니다.";
  refreshPostButton();
  syncBlockNote();
  try {
    const draft = await api(`/api/drafts/latest?video_id=${encodeURIComponent(videoId)}`);
    if (token !== watchToken || state.selectedId !== videoId) return;
    fillDraft(draft);
  } catch {
    if (token === watchToken) refreshPostButton();
  }
}

async function createDraft() {
  if (!state.selectedId) return;
  const videoId = state.selectedId;
  const token = ++watchToken;
  $("make-draft").disabled = true;
  $("redo").disabled = true;
  $("draft-status").textContent = "영상 내용을 읽고 초안을 만드는 중입니다.";
  try {
    const job = await api("/api/drafts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: videoId }),
    });
    const done = await watch(job.id, token);
    if (!done || token !== watchToken || state.selectedId !== videoId) return;
    if (done.status === "error") {
      $("draft-status").textContent = done.error || "초안을 만들지 못했습니다.";
      return;
    }
    fillDraft(done.result || {});
  } catch (error) {
    if (token === watchToken && state.selectedId === videoId) {
      $("draft-status").textContent = error.message;
    }
  } finally {
    if (token === watchToken) refreshPostButton();
  }
}

async function loadPosts() {
  const data = await api("/api/posts");
  applyLimits(data);
  const root = $("log");
  const posts = data.posts || [];
  if (!posts.length) {
    root.innerHTML = `<p class="note log-empty">아직 기록이 없습니다.</p>`;
    return;
  }
  const items = posts.map((post) => {
    const when = esc(post.created_label || "");
    const mark = post.status === "success" ? "성공" : "실패";
    const shot = post.screenshot
      ? ` <a href="/api/shots/${esc(post.screenshot)}" target="_blank">화면 캡처</a>`
      : "";
    return `<li class="${esc(post.status)}"><time datetime="${esc(post.created_at)}">${when} · ${mark}</time><p>${esc(post.sentence)}${shot}</p></li>`;
  }).join("");
  root.innerHTML = `<ol class="log-list">${items}</ol>`;
}

async function loadHealth() {
  try {
    const data = await api("/api/health");
    state.limits.min = data.min_chars;
    state.limits.max = data.max_chars;
    state.limits.brand = data.brand;
    setHealth(data.ok, data.detail, data.model);
    applyLimits(data);
    const box = $("profiles");
    if (!box.dataset.ready) {
      const options = data.profiles.map((profile, index) =>
        `<label><input type="radio" name="profile" value="${esc(profile.id)}" ${index === 0 ? "checked" : ""}/> ${esc(profile.label)}</label>`
      );
      options.push(`<label><input type="radio" name="profile" value="rotate"/> 돌아가며 배정</label>`);
      box.innerHTML = options.join("");
      box.dataset.ready = "1";
      renderQuota();
    }
  } catch (error) {
    setHealth(false, error.message);
  }
}

$("fetch").addEventListener("click", async () => {
  $("fetch").disabled = true;
  $("fetch-note").textContent = "채널 영상을 불러오는 중입니다.";
  try {
    const data = await api("/api/videos/fetch", { method: "POST" });
    watchToken += 1;
    state.videos = data.videos;
    state.risingOnly = false;
    state.selectedId = null;
    state.draft = null;
    const warnings = (data.warnings || []).join(" ");
    $("fetch-note").textContent = warnings || `${data.videos.length}개 영상을 불러왔습니다.`;
    renderVideos();
    renderPicked();
    $("comment").value = "";
    $("concern").textContent = "";
    $("evidence").textContent = "";
    $("draft-status").textContent = "";
    refreshPostButton();
  } catch (error) {
    $("fetch-note").textContent = error.message;
  } finally {
    $("fetch").disabled = false;
  }
});

$("rising").addEventListener("click", async () => {
  $("rising").disabled = true;
  $("fetch-note").textContent = "최근 영상의 조회수를 확인하는 중입니다.";
  try {
    const data = await api("/api/videos/rising", { method: "POST" });
    watchToken += 1;
    state.videos = data.videos;
    state.risingOnly = true;
    state.selectedId = null;
    state.draft = null;
    const extra = data.note || "";
    $("fetch-note").textContent = [extra, ...(data.warnings || [])].filter(Boolean).join(" ");
    renderVideos();
    renderPicked();
    $("comment").value = "";
    $("concern").textContent = "";
    $("evidence").textContent = "";
    $("draft-status").textContent = "";
    refreshPostButton();
  } catch (error) {
    $("fetch-note").textContent = error.message;
  } finally {
    $("rising").disabled = false;
  }
});

$("link-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("draft-status").textContent = "링크의 채널을 확인하는 중입니다.";
  try {
    const data = await api("/api/videos/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: $("link").value }),
    });
    const video = data.video;
    state.videos = [video, ...state.videos.filter((item) => item.video_id !== video.video_id)];
    state.risingOnly = false;
    selectVideo(video.video_id, false);
  } catch (error) {
    $("draft-status").textContent = error.message;
  }
});

$("make-draft").addEventListener("click", () => createDraft());
$("redo").addEventListener("click", () => createDraft());

$("comment").addEventListener("input", () => {
  refreshPostButton();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveComment($("comment").value), 400);
});

$("post").addEventListener("click", async () => {
  const reason = blockReason();
  if (reason) {
    $("post-status").textContent = reason;
    return;
  }
  $("post").disabled = true;
  $("shot").hidden = true;
  const token = ++watchToken;
  try {
    const job = await api("/api/posts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_id: state.selectedId,
        profile_id: selectedProfile(),
        comment: $("comment").value.trim(),
      }),
    });
    setStep(job.step || "프로필 여는 중");
    const done = await watch(job.id, token);
    if (!done) return;
    if (done.status === "error") {
      $("post-status").textContent = done.error || "게시에 실패했습니다.";
      if (done.screenshot) {
        $("shot").hidden = false;
        $("shot").src = `/api/shots/${done.screenshot}`;
      }
    } else {
      setStep("게시 완료 · 댓글 보기");
    }
    await loadPosts();
    await loadHealth();
    const current = state.videos.find((item) => item.video_id === state.selectedId);
    if (current && done.status === "done") {
      const used = (done.result && done.result.profile_id) || "";
      if (used) {
        const names = new Set(current.posted_profiles || []);
        names.add(used);
        current.posted_profiles = [...names];
      }
    }
    renderVideos();
    refreshPostButton();
  } catch (error) {
    $("post-status").textContent = error.message;
    refreshPostButton();
  }
});

$("livewiki-login").addEventListener("click", async () => {
  $("livewiki-login").disabled = true;
  const token = ++watchToken;
  try {
    const job = await api("/api/livewiki/login", { method: "POST" });
    const done = await watch(job.id, token);
    if (!done) return;
    if (done.status === "error") $("draft-status").textContent = done.error || "LiveWiki 창을 열지 못했습니다.";
    if (done.status === "done") $("draft-status").textContent = "LiveWiki 로그인 창을 닫았습니다.";
  } catch (error) {
    $("draft-status").textContent = error.message;
  } finally {
    $("livewiki-login").disabled = false;
  }
});

$("login").addEventListener("click", async () => {
  const profileId = selectedProfile();
  if (profileId === "rotate") {
    $("post-status").textContent = "로그인할 프로필을 하나 고르세요.";
    return;
  }
  $("login").disabled = true;
  const token = ++watchToken;
  try {
    const job = await api(`/api/profiles/${profileId}/login`, { method: "POST" });
    const done = await watch(job.id, token);
    if (done && done.status === "error") $("post-status").textContent = done.error;
    if (done && done.status === "done") $("post-status").textContent = "로그인 창을 닫았습니다.";
  } catch (error) {
    $("post-status").textContent = error.message;
  } finally {
    $("login").disabled = false;
  }
});

$("profiles").addEventListener("change", () => {
  renderQuota();
  syncBlockNote();
});

renderVideos();
refreshPostButton();
loadHealth();
loadPosts().catch(() => {});
setInterval(loadHealth, 20000);
