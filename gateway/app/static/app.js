const $ = (selector) => document.querySelector(selector);

const elements = {
  form: $("#job-form"),
  file: $("#file"),
  dropZone: $("#drop-zone"),
  pasteImage: $("#paste-image"),
  inputPreview: $("#input-preview"),
  fileList: $("#file-list"),
  changeFile: $("#change-file"),
  clearFiles: $("#clear-files"),
  pipeline: $("#pipeline"),
  alpha: $("#alpha"),
  alphaOutput: $("#alpha-output"),
  scale: $("#scale"),
  colorCorrection: $("#color-correction"),
  seed: $("#seed"),
  randomSeed: $("#random-seed"),
  submit: $("#submit"),
  formError: $("#form-error"),
  health: $("#health"),
  activeJob: $("#active-job"),
  jobStage: $("#job-stage"),
  jobStatus: $("#job-status"),
  progressBar: $("#progress-bar"),
  jobError: $("#job-error"),
  comparison: $("#comparison"),
  batchResults: $("#batch-results"),
  batchResultList: $("#batch-result-list"),
  beforeImage: $("#before-image"),
  afterImage: $("#after-image"),
  afterClip: $("#after-clip"),
  divider: $("#divider"),
  compareSlider: $("#compare-slider"),
  comparePercent: $("#compare-percent"),
  previousImage: $("#previous-image"),
  nextImage: $("#next-image"),
  download: $("#download"),
  copyOriginal: $("#copy-original"),
  copyResult: $("#copy-result"),
  copyStatus: $("#copy-status"),
  recentJobs: $("#recent-jobs"),
  refreshJobs: $("#refresh-jobs"),
  loadMoreJobs: $("#load-more-jobs"),
};

const RECENT_JOBS_PAGE_SIZE = 20;
let selectedFiles = [];
let previewUrls = [];
let pasteFallbackTimer = null;
let batchResults = [];
let recentComparisonJobs = [];
let recentJobsLimit = RECENT_JOBS_PAGE_SIZE;
let activePollToken = 0;
let activeComparisonJobId = null;

function isSupportedImage(file) {
  if (!file) return false;
  if (file.type?.startsWith("image/")) return true;
  return /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(file.name || "");
}

function humanBytes(bytes) {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function showError(element, message) {
  element.textContent = message;
  element.classList.toggle("hidden", !message);
}

function renderSelectedFiles() {
  previewUrls.forEach((url) => URL.revokeObjectURL(url));
  previewUrls = [];
  elements.fileList.replaceChildren(...selectedFiles.map((file, index) => {
    const row = document.createElement("div");
    row.className = "file-card";
    const image = document.createElement("img");
    image.alt = `${file.name} preview`;
    const previewUrl = URL.createObjectURL(file);
    previewUrls.push(previewUrl);
    image.src = previewUrl;
    const details = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = file.name;
    const meta = document.createElement("span");
    meta.textContent = humanBytes(file.size);
    details.append(name, meta);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-file";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      selectedFiles.splice(index, 1);
      renderSelectedFiles();
    });
    row.append(image, details, remove);
    return row;
  }));
  const hasFiles = selectedFiles.length > 0;
  elements.dropZone.classList.toggle("hidden", hasFiles);
  elements.inputPreview.classList.toggle("hidden", !hasFiles);
  elements.submit.disabled = !hasFiles;
  elements.submit.textContent = "Defractolize!";
}

function selectFiles(files) {
  const images = Array.from(files || []).filter(isSupportedImage);
  if (!images.length) {
    showError(elements.formError, "Choose one or more supported image files.");
    return;
  }
  const existing = new Set(selectedFiles.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
  selectedFiles.push(...images.filter((file) => {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (existing.has(key)) return false;
    existing.add(key);
    return true;
  }));
  renderSelectedFiles();
  showError(elements.formError, "");
}

function clearFiles() {
  selectedFiles = [];
  elements.file.value = "";
  renderSelectedFiles();
}

function clipboardExtension(mimeType) {
  return { "image/jpeg": "jpg", "image/webp": "webp", "image/bmp": "bmp", "image/tiff": "tiff" }[mimeType] || "png";
}

async function readClipboardImage() {
  if (!navigator.clipboard?.read) {
    throw new Error("This browser does not allow reading images from the clipboard.");
  }
  const items = await Promise.race([
    navigator.clipboard.read(),
    new Promise((_, reject) => setTimeout(() => reject(new Error("Clipboard access timed out. Use the Paste from clipboard button and allow access when prompted.")), 10000)),
  ]);
  for (const item of items) {
    const imageType = item.types.find((type) => type.startsWith("image/"));
    if (!imageType) continue;
    const blob = await item.getType(imageType);
    const filename = `pasted-${new Date().toISOString().replace(/[:.]/g, "-")}.${clipboardExtension(imageType)}`;
    selectFiles([new File([blob], filename, { type: imageType, lastModified: Date.now() })]);
    return true;
  }
  throw new Error("No image was found in the clipboard.");
}

function reportPasteError(error) {
  showError(elements.formError, error.message || "Could not read an image from the clipboard.");
}

function requestClipboardImage() {
  readClipboardImage().catch(reportPasteError);
}

function updatePipelineControls() {
  const pipeline = elements.pipeline.value;
  const hasCleaner = pipeline.includes("cleaner");
  const hasSeed = pipeline.includes("seedvr2");
  $("#alpha-control").classList.toggle("disabled-control", !hasCleaner);
  $("#scale-control").classList.toggle("disabled-control", !hasSeed);
  $("#color-control").classList.toggle("disabled-control", !hasSeed);
  $("#seed-control").classList.toggle("disabled-control", !hasSeed);
}

function updateComparison() {
  const percent = Number(elements.compareSlider.value);
  const width = elements.beforeImage.clientWidth;
  elements.afterClip.style.width = `${100 - percent}%`;
  elements.divider.style.left = `${percent}%`;
  elements.afterImage.style.setProperty("--compare-width", `${width}px`);
  elements.afterImage.style.transform = `translateX(-${width * (percent / 100)}px)`;
  elements.comparePercent.value = `${100 - percent}% after`;
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error("Gateway unavailable");
    const data = await response.json();
    const services = Object.entries(data.backends);
    const ready = services.every(([, service]) => service.status === "ok");
    elements.health.className = `health ${ready ? "ok" : "bad"}`;
    elements.health.querySelector("span:last-child").textContent = ready
      ? `GPU services ready · queue ${data.queue_size}`
      : services.map(([name, service]) => `${name}: ${service.status}`).join(" · ");
  } catch (error) {
    elements.health.className = "health bad";
    elements.health.querySelector("span:last-child").textContent = "Services unavailable";
  }
}

function renderJob(job, batchLabel = "") {
  elements.activeJob.classList.remove("hidden");
  elements.jobStage.textContent = batchLabel ? `${batchLabel} · ${job.stage}` : job.stage;
  elements.jobStatus.textContent = job.status;
  elements.progressBar.style.width = `${job.progress || 0}%`;
  showError(elements.jobError, job.error || "");

  if (job.status === "completed") {
    activeComparisonJobId = job.id;
    const cacheBust = encodeURIComponent(job.updated_at);
    elements.beforeImage.src = `/api/jobs/${job.id}/input`;
    elements.afterImage.src = `/api/jobs/${job.id}/result?v=${cacheBust}`;
    elements.download.href = `/api/jobs/${job.id}/result`;
    elements.download.download = `${job.original_filename.replace(/\.[^.]+$/, "")}-restored.png`;
    elements.copyOriginal.disabled = false;
    elements.copyResult.disabled = false;
    elements.copyStatus.textContent = "";
    elements.comparison.classList.remove("hidden");
    elements.afterImage.onload = updateComparison;
    elements.beforeImage.onload = updateComparison;
    updateImageNavigation();
  } else {
    elements.copyOriginal.disabled = true;
    elements.copyResult.disabled = true;
    elements.comparison.classList.add("hidden");
  }
}

function imageNavigationPool() {
  if (batchResults.some((job) => job.id === activeComparisonJobId)) {
    return { jobs: batchResults, label: "Image" };
  }
  if (recentComparisonJobs.some((job) => job.id === activeComparisonJobId)) {
    return { jobs: recentComparisonJobs, label: "Saved image" };
  }
  return { jobs: [], label: "Image" };
}

function updateImageNavigation() {
  const { jobs } = imageNavigationPool();
  const currentIndex = jobs.findIndex((job) => job.id === activeComparisonJobId);
  const canNavigate = jobs.length > 1 && currentIndex >= 0;
  elements.previousImage.classList.toggle("hidden", !canNavigate);
  elements.nextImage.classList.toggle("hidden", !canNavigate);
  if (!canNavigate) {
    elements.previousImage.removeAttribute("title");
    elements.nextImage.removeAttribute("title");
    return;
  }
  const previousIndex = (currentIndex - 1 + jobs.length) % jobs.length;
  const nextIndex = (currentIndex + 1) % jobs.length;
  elements.previousImage.title = `View image ${previousIndex + 1} of ${jobs.length}`;
  elements.nextImage.title = `View image ${nextIndex + 1} of ${jobs.length}`;
}

function showAdjacentImage(offset) {
  const { jobs, label } = imageNavigationPool();
  if (!jobs.length) return;
  const currentIndex = jobs.findIndex((job) => job.id === activeComparisonJobId);
  const normalizedIndex = ((currentIndex + offset) % jobs.length + jobs.length) % jobs.length;
  renderJob(jobs[normalizedIndex], `${label} ${normalizedIndex + 1} of ${jobs.length}`);
  elements.activeJob.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showBatchResult(index) {
  if (!batchResults.length) return;
  const normalizedIndex = ((index % batchResults.length) + batchResults.length) % batchResults.length;
  const job = batchResults[normalizedIndex];
  renderJob(job, `Image ${normalizedIndex + 1} of ${batchResults.length}`);
  elements.activeJob.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderBatchResults() {
  const hasResults = batchResults.length > 0;
  elements.batchResults.classList.toggle("hidden", !hasResults);
  if (!hasResults) {
    elements.batchResultList.replaceChildren();
    return;
  }
  elements.batchResultList.replaceChildren(...batchResults.map((job, index) => {
    const card = document.createElement("article");
    card.className = "batch-result-card";
    const thumbs = document.createElement("div");
    thumbs.className = "batch-thumbs";
    const before = document.createElement("img");
    before.src = `/api/jobs/${job.id}/input?batch=${encodeURIComponent(job.updated_at)}`;
    before.alt = `${job.original_filename} before`;
    const after = document.createElement("img");
    after.src = `/api/jobs/${job.id}/result?batch=${encodeURIComponent(job.updated_at)}`;
    after.alt = `${job.original_filename} after`;
    thumbs.append(before, after);
    const details = document.createElement("div");
    details.className = "batch-result-details";
    const name = document.createElement("strong");
    name.textContent = `${index + 1}. ${job.original_filename}`;
    const meta = document.createElement("small");
    meta.textContent = "Before → After";
    const view = document.createElement("button");
    view.type = "button";
    view.className = "secondary";
    view.textContent = "View comparison";
    view.addEventListener("click", () => showBatchResult(index));
    details.append(name, meta, view);
    card.append(thumbs, details);
    return card;
  }));
  updateImageNavigation();
}

function clearBatchResults() {
  batchResults = [];
  activeComparisonJobId = null;
  renderBatchResults();
  updateImageNavigation();
}

function containsDraggedFiles(event) {
  const types = Array.from(event.dataTransfer?.types || []);
  return types.includes("Files") || Boolean(event.dataTransfer?.files?.length);
}

function bindFileDropTarget(target) {
  let dragDepth = 0;
  target.addEventListener("dragenter", (event) => {
    if (!containsDraggedFiles(event)) return;
    event.preventDefault();
    dragDepth += 1;
    target.classList.add("dragging");
  });
  target.addEventListener("dragover", (event) => {
    if (!containsDraggedFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    target.classList.add("dragging");
  });
  target.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) target.classList.remove("dragging");
  });
  target.addEventListener("drop", (event) => {
    if (!containsDraggedFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    target.classList.remove("dragging");
    selectFiles(event.dataTransfer.files);
  });
}

function pause(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForJob(jobId, batchLabel = "", isCurrent = () => true) {
  while (true) {
    if (!isCurrent()) return null;
    const response = await fetch(`/api/jobs/${jobId}`);
    if (!response.ok) throw new Error("Could not read job status");
    const job = await response.json();
    if (!isCurrent()) return null;
    renderJob(job, batchLabel);
    if (job.status !== "queued" && job.status !== "running") {
      await loadJobs();
      await checkHealth();
      return job;
    }
    await pause(1000);
  }
}

function pollJob(jobId) {
  const pollToken = ++activePollToken;
  waitForJob(jobId, "", () => pollToken === activePollToken).catch((error) => {
    if (pollToken !== activePollToken) return;
    showError(elements.jobError, error.message);
    elements.submit.disabled = selectedFiles.length === 0;
  });
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

async function loadJobs() {
  elements.loadMoreJobs.disabled = true;
  try {
    const response = await fetch(`/api/jobs?limit=${recentJobsLimit + 1}`);
    if (!response.ok) throw new Error("Could not load jobs");
    const availableJobs = await response.json();
    const hasMoreJobs = availableJobs.length > recentJobsLimit;
    const jobs = availableJobs.slice(0, recentJobsLimit);
    recentComparisonJobs = jobs.filter((job) => job.status === "completed" && job.result_available);
    updateImageNavigation();
    elements.loadMoreJobs.classList.toggle("hidden", !hasMoreJobs);
    if (!jobs.length) {
      elements.recentJobs.innerHTML = '<p class="muted">No jobs yet.</p>';
      return;
    }
    elements.recentJobs.replaceChildren(...jobs.map((job) => {
      const row = document.createElement("article");
      row.className = "job-row";
      const openJob = () => {
        activePollToken += 1;
        clearBatchResults();
        renderJob(job);
        if (job.status === "queued" || job.status === "running") pollJob(job.id);
        elements.activeJob.scrollIntoView({ behavior: "smooth", block: "start" });
      };
      const previewButton = document.createElement("button");
      previewButton.type = "button";
      previewButton.className = "job-preview-button";
      previewButton.setAttribute("aria-label", `Open ${job.original_filename}`);
      if (job.status === "completed" && job.result_available) {
        const preview = document.createElement("img");
        preview.className = "job-preview";
        preview.src = `/api/jobs/${job.id}/preview?v=${encodeURIComponent(job.updated_at)}`;
        preview.alt = `${job.original_filename} restored preview`;
        preview.loading = "lazy";
        preview.decoding = "async";
        preview.addEventListener("error", () => {
          const unavailable = document.createElement("span");
          unavailable.className = "job-preview-placeholder";
          unavailable.textContent = "Preview unavailable";
          previewButton.replaceChildren(unavailable);
        }, { once: true });
        previewButton.append(preview);
      } else {
        const pending = document.createElement("span");
        pending.className = "job-preview-placeholder";
        pending.textContent = job.status === "failed" ? "No result" : "Result pending";
        previewButton.append(pending);
      }
      previewButton.addEventListener("click", openJob);
      const details = document.createElement("div");
      details.className = "job-details";
      const name = document.createElement("strong");
      name.textContent = job.original_filename;
      const meta = document.createElement("small");
      meta.textContent = `${job.options.pipeline.replaceAll("_", " → ")} · ${job.status}`;
      const footer = document.createElement("div");
      footer.className = "job-card-footer";
      const date = document.createElement("small");
      date.className = "job-date";
      date.textContent = formatDate(job.created_at);
      const open = document.createElement("button");
      open.type = "button";
      open.className = "secondary";
      open.textContent = "Open";
      open.addEventListener("click", openJob);
      footer.append(date, open);
      details.append(name, meta, footer);
      row.append(previewButton, details);
      return row;
    }));
  } catch (error) {
    elements.recentJobs.innerHTML = `<p class="error">${error.message}</p>`;
  } finally {
    elements.loadMoreJobs.disabled = false;
    elements.loadMoreJobs.textContent = "Load more images";
  }
}

async function convertBlobToPng(blob) {
  if (blob.type === "image/png") return blob;
  const bitmap = await createImageBitmap(blob);
  try {
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Could not prepare the image for copying");
    context.drawImage(bitmap, 0, 0);
    return await new Promise((resolve, reject) => {
      canvas.toBlob(
        (png) => png ? resolve(png) : reject(new Error("Could not convert the image to PNG")),
        "image/png",
      );
    });
  } finally {
    bitmap.close();
  }
}

async function copyImageToClipboard(source, label) {
  if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") {
    elements.copyStatus.textContent = "Clipboard image copy is not supported here.";
    return;
  }
  elements.copyOriginal.disabled = true;
  elements.copyResult.disabled = true;
  elements.copyStatus.textContent = `Copying ${label.toLowerCase()}…`;
  try {
    const response = await fetch(source);
    if (!response.ok) throw new Error(`Could not read the ${label.toLowerCase()} image`);
    const png = await convertBlobToPng(await response.blob());
    await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
    elements.copyStatus.textContent = `${label} copied to clipboard`;
  } catch (error) {
    elements.copyStatus.textContent = error.message || `Could not copy the ${label.toLowerCase()}`;
  } finally {
    elements.copyOriginal.disabled = false;
    elements.copyResult.disabled = false;
  }
}

elements.file.addEventListener("change", () => {
  selectFiles(elements.file.files);
  elements.file.value = "";
});
elements.changeFile.addEventListener("click", () => elements.file.click());
elements.pasteImage.addEventListener("click", requestClipboardImage);
elements.clearFiles.addEventListener("click", clearFiles);
bindFileDropTarget(elements.dropZone);
bindFileDropTarget(elements.inputPreview);
window.addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "v") return;
  if (event.target instanceof HTMLInputElement && event.target.type !== "file") return;
  clearTimeout(pasteFallbackTimer);
  pasteFallbackTimer = setTimeout(() => {
    pasteFallbackTimer = null;
    requestClipboardImage();
  }, 150);
}, true);
window.addEventListener("paste", (event) => {
  clearTimeout(pasteFallbackTimer);
  pasteFallbackTimer = null;
  const imageItem = Array.from(event.clipboardData?.items || []).find((item) => item.type.startsWith("image/"));
  if (imageItem) {
    event.preventDefault();
    selectFiles([imageItem.getAsFile()]);
    return;
  }
  if (event.clipboardData) return;
  requestClipboardImage();
}, true);
elements.pipeline.addEventListener("change", updatePipelineControls);
elements.alpha.addEventListener("input", () => {
  elements.alphaOutput.value = Number(elements.alpha.value).toFixed(2);
});
elements.randomSeed.addEventListener("click", () => {
  elements.seed.value = String(Math.floor(Math.random() * Number.MAX_SAFE_INTEGER));
});
elements.compareSlider.addEventListener("input", updateComparison);
window.addEventListener("resize", updateComparison);
elements.refreshJobs.addEventListener("click", loadJobs);
elements.loadMoreJobs.addEventListener("click", async () => {
  recentJobsLimit += RECENT_JOBS_PAGE_SIZE;
  elements.loadMoreJobs.textContent = "Loading…";
  await loadJobs();
});
elements.previousImage.addEventListener("click", () => showAdjacentImage(-1));
elements.nextImage.addEventListener("click", () => showAdjacentImage(1));
elements.copyOriginal.addEventListener("click", () => {
  copyImageToClipboard(elements.beforeImage.src, "Original");
});
elements.copyResult.addEventListener("click", () => {
  copyImageToClipboard(elements.afterImage.src, "Result");
});

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedFiles.length) return;
  activePollToken += 1;
  elements.submit.disabled = true;
  showError(elements.formError, "");
  elements.comparison.classList.add("hidden");
  elements.copyStatus.textContent = "";
  const files = selectedFiles.slice();
  const failures = [];
  clearBatchResults();
  try {
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      try {
        const body = new FormData();
        body.append("file", file);
        body.append("pipeline", elements.pipeline.value);
        body.append("alpha", elements.alpha.value);
        body.append("scale", elements.scale.value);
        body.append("color_correction", elements.colorCorrection.value);
        body.append("seed", elements.seed.value);
        elements.activeJob.classList.remove("hidden");
        elements.jobStage.textContent = `Submitting image ${index + 1} of ${files.length}`;
        elements.jobStatus.textContent = "submitting";
        elements.progressBar.style.width = "0%";
        const response = await fetch("/api/jobs", { method: "POST", body });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `Job ${index + 1} submission failed`);
        const job = await waitForJob(data.id, files.length > 1 ? `Image ${index + 1} of ${files.length}` : "");
        if (job.status === "completed" && files.length > 1) {
          batchResults.push(job);
          renderBatchResults();
        }
        if (job.status === "failed") failures.push(`${file.name}: ${job.error || "failed"}`);
      } catch (error) {
        failures.push(`${file.name}: ${error.message || "submission failed"}`);
      }
    }
    if (failures.length) {
      showError(elements.formError, failures.join("; "));
      if (files.length > 1) {
        elements.activeJob.classList.remove("hidden");
        elements.jobStage.textContent = `Batch finished · ${files.length - failures.length} of ${files.length} completed`;
        elements.jobStatus.textContent = "completed with errors";
      } else {
        elements.jobStage.textContent = "Submission failed";
        elements.jobStatus.textContent = "failed";
      }
    }
  } catch (error) {
    showError(elements.formError, error.message);
  } finally {
    elements.submit.disabled = selectedFiles.length === 0;
  }
});

updatePipelineControls();
checkHealth();
loadJobs();
setInterval(checkHealth, 15000);
