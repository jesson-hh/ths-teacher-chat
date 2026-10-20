"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const list = byId("message-list");
  const scroll = byId("feed-scroll");
  const empty = byId("empty-state");
  const newButton = byId("new-messages");
  const desktop = new URLSearchParams(location.hash.slice(1)).get("desktop") === "1";
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "long", day: "numeric", weekday: "short" });
  const timeFormat = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false });
  const dayFormat = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" });
  let ids = [];
  let pending = false;
  let teacherName = "老师";

  function goLatest() {
    scroll.scrollTop = scroll.scrollHeight;
    newButton.hidden = true;
  }

  function connection(kind, label) {
    byId("connection-dot").className = `connection-dot ${kind}`;
    byId("connection-label").textContent = label;
  }

  function updateStatus(status) {
    teacherName = status.teacher_name || "老师";
    byId("teacher-name").textContent = teacherName;
    byId("identity-method").textContent = desktop ? "程序内接收" : status.identity_mode === "dom_teacher" ? "公开消息 · 页面识别" : "公开消息";
    byId("message-count").textContent = `${status.message_count} 条`;
    if (!status.configured) {
      connection("waiting", "等待老师身份配对");
      byId("empty-title").textContent = "等待老师消息来源配对";
      byId("empty-description").textContent = "在组件配置中选定老师，再打开约牛公开聊天室。配对后这里只显示该老师的公开发言。";
    } else if (status.freshness === "fresh") {
      connection("fresh", "采集已连接");
      byId("empty-title").textContent = "等待老师消息";
      byId("empty-description").textContent = "已连接老师公开聊天室。老师发言后，消息会自动显示在这里。";
    } else if (status.freshness === "stale") {
      connection("stale", "消息同步已暂停");
      byId("empty-title").textContent = "等待恢复消息同步";
      byId("empty-description").textContent = desktop ? "点击上方“约牛登录”检查登录状态，或点击“重新连接”。" : "请保持已配对的约牛公开聊天室打开，组件会自动继续同步。";
    } else {
      connection("waiting", desktop ? "等待程序内登录约牛" : "等待约牛公开聊天室");
      byId("empty-title").textContent = desktop ? "登录后查看老师消息" : "等待老师消息";
      byId("empty-description").textContent = desktop ? "点击上方“约牛登录”，在程序内完成登录后，这里会自动显示老师发言。" : "打开已配对的约牛公开聊天室后，老师的公开消息会显示在这里。";
    }
    byId("last-sync").textContent = status.last_seen
      ? `最近同步 ${timeFormat.format(new Date(status.last_seen))}`
      : "尚未连接消息采集";
  }

  function imageButton(url, index) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "message-image";
    button.setAttribute("aria-label", `放大第 ${index + 1} 张消息图片`);
    const img = document.createElement("img");
    img.alt = `老师消息图片 ${index + 1}`;
    img.src = url;
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    img.addEventListener("error", () => button.classList.add("image-error"));
    button.append(img);
    button.addEventListener("click", () => {
      const expanded = byId("expanded-image");
      expanded.referrerPolicy = "no-referrer";
      expanded.src = url;
      byId("image-dialog").showModal();
    });
    return button;
  }

  function renderMessages(messages) {
    const nextIds = messages.map((message) => message.id);
    if (nextIds.length === ids.length && nextIds.every((id, index) => id === ids[index])) return;
    const wasAtBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
    const oldTop = scroll.scrollTop;
    const hadMessages = ids.length > 0;
    const newCount = nextIds.filter((id) => !ids.includes(id)).length;
    const fragment = document.createDocumentFragment();
    let lastDay = "";
    for (const message of messages) {
      const published = new Date(message.published_at);
      const day = dayFormat.format(published);
      if (day !== lastDay) {
        const divider = document.createElement("div");
        divider.className = "day-divider";
        divider.textContent = dateFormat.format(published);
        fragment.append(divider);
        lastDay = day;
      }
      const article = document.createElement("article");
      article.className = "message";
      const meta = document.createElement("div");
      meta.className = "message-meta";
      const author = document.createElement("span");
      author.className = "message-author";
      author.textContent = "老师";
      const time = document.createElement("time");
      time.className = "message-time";
      time.dateTime = message.published_at;
      time.textContent = timeFormat.format(published);
      meta.append(author, time);
      const content = document.createElement("div");
      content.className = "message-content";
      if (message.text) {
        const text = document.createElement("p");
        text.className = "message-text";
        text.textContent = message.text;
        content.append(text);
      }
      if (message.images.length) {
        const images = document.createElement("div");
        images.className = `message-images${message.images.length === 1 ? " single" : ""}`;
        message.images.forEach((url, index) => images.append(imageButton(url, index)));
        content.append(images);
      }
      article.append(meta, content);
      fragment.append(article);
    }
    list.replaceChildren(fragment);
    ids = nextIds;
    empty.hidden = ids.length > 0;
    if (wasAtBottom || !hadMessages) {
      goLatest();
    } else {
      scroll.scrollTop = oldTop;
      if (newCount) {
        newButton.textContent = `${newCount} 条新消息 ↓`;
        newButton.hidden = false;
      }
    }
  }

  async function refresh() {
    if (pending) return;
    pending = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch("/api/messages", { cache: "no-store", credentials: "omit", signal: controller.signal });
      if (!response.ok) throw new Error("Local component unavailable");
      const payload = await response.json();
      updateStatus(payload.status);
      renderMessages(payload.messages);
    } catch {
      connection("error", "本机组件暂未连接");
      if (!ids.length) {
        byId("empty-title").textContent = "等待启动老师消息组件";
        byId("empty-description").textContent = "启动本机组件后会自动重连，已显示的消息可继续阅读。";
      }
    } finally {
      window.clearTimeout(timeout);
      pending = false;
    }
  }

  byId("latest").addEventListener("click", goLatest);
  newButton.addEventListener("click", goLatest);
  scroll.addEventListener("scroll", () => {
    if (scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 35) newButton.hidden = true;
  }, { passive: true });
  byId("font-size").addEventListener("click", () => {
    const large = document.documentElement.dataset.large !== "true";
    document.documentElement.dataset.large = String(large);
    document.documentElement.style.setProperty("--body-size", large ? "17px" : "15px");
    byId("font-size").setAttribute("aria-label", large ? "恢复消息字号" : "放大消息字号");
  });
  byId("close-image").addEventListener("click", () => byId("image-dialog").close());
  byId("image-dialog").addEventListener("click", (event) => {
    if (event.target === byId("image-dialog")) byId("image-dialog").close();
  });
  byId("image-dialog").addEventListener("close", () => byId("expanded-image").removeAttribute("src"));
  window.setInterval(refresh, 2000);
  refresh();
})();
