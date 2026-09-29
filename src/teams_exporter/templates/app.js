(() => {
  "use strict";

  const CONVERSATION_PAGE_SIZE = 100;
  let receiveMessageChunk = null;

  window.TeamsExporterMessageChunk = (payload) => {
    if (receiveMessageChunk) receiveMessageChunk(payload);
  };

  const normalize = (value) => (value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase();

  function initializeSidebar() {
    const sidebar = document.querySelector("[data-sidebar]");
    if (!sidebar) return;

    const list = sidebar.querySelector("[data-conversation-list]");
    const items = Array.from(sidebar.querySelectorAll("[data-conversation-item]"));
    const search = sidebar.querySelector("[data-conversation-search]");
    const sort = sidebar.querySelector("[data-conversation-sort]");
    const messageFilter = sidebar.querySelector("[data-message-filter]");
    const personFilter = sidebar.querySelector("[data-person-filter]");
    const typeFilter = sidebar.querySelector("[data-type-filter]");
    const density = sidebar.querySelector("[data-density]");
    const reset = sidebar.querySelector("[data-reset-filters]");
    const summary = sidebar.querySelector("[data-result-summary]");
    const loadMore = sidebar.querySelector("[data-load-more]");
    const activeItem = sidebar.querySelector("[data-conversation-item].active");
    let visibleLimit = CONVERSATION_PAGE_SIZE;
    let scheduled = false;
    let firstUpdate = true;

    for (const item of items) {
      item._normalizedSearch = normalize(item.dataset.search);
      item._normalizedPeople = normalize(item.dataset.people);
      item._normalizedTitle = normalize(item.dataset.title);
    }

    const compareNumbers = (left, right, name) =>
      Number(right.dataset[name]) - Number(left.dataset[name]);

    function compare(left, right) {
      let result = 0;
      if (sort.value === "recent") {
        result = (right.dataset.lastActivity || "").localeCompare(
          left.dataset.lastActivity || ""
        );
      } else if (sort.value === "title") {
        result = left._normalizedTitle.localeCompare(right._normalizedTitle);
      } else if (sort.value === "people") {
        result = compareNumbers(left, right, "personCount");
      } else {
        result = compareNumbers(left, right, "messageCount");
      }
      return result || Number(left.dataset.originalIndex) - Number(right.dataset.originalIndex);
    }

    function matches(item) {
      const query = normalize(search.value.trim());
      const person = normalize(personFilter.value.trim());
      const messageCount = Number(item.dataset.messageCount);
      return (!query || item._normalizedSearch.includes(query))
        && (!person || item._normalizedPeople.includes(person))
        && (typeFilter.value === "all" || item.dataset.chatType === typeFilter.value)
        && (messageFilter.value === "all"
          || (messageFilter.value === "with" && messageCount > 0)
          || (messageFilter.value === "empty" && messageCount === 0));
    }

    function update() {
      scheduled = false;
      const matching = items.filter(matches).sort(compare);
      const matchingSet = new Set(matching);
      const fragment = document.createDocumentFragment();

      for (const item of matching) fragment.append(item);
      for (const item of items) {
        if (!matchingSet.has(item)) fragment.append(item);
      }
      list.append(fragment);

      const visibleItems = matching.slice(0, visibleLimit);
      if (activeItem && matchingSet.has(activeItem) && !visibleItems.includes(activeItem)) {
        visibleItems.push(activeItem);
      }
      const visibleSet = new Set(visibleItems);
      for (const item of items) {
        item.hidden = !visibleSet.has(item);
      }

      summary.textContent = matching.length
        ? `Showing ${visibleItems.length} of ${matching.length} matching conversation(s)`
        : "No matching conversations";
      loadMore.hidden = visibleItems.length >= matching.length;
      if (firstUpdate && activeItem && !activeItem.hidden) {
        window.requestAnimationFrame(() => activeItem.scrollIntoView({ block: "nearest" }));
      }
      firstUpdate = false;
    }

    function scheduleUpdate(resetLimit = true) {
      if (resetLimit) visibleLimit = CONVERSATION_PAGE_SIZE;
      if (scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(update);
    }

    for (const control of [search, personFilter]) {
      control.addEventListener("input", () => scheduleUpdate());
    }
    for (const control of [sort, messageFilter, typeFilter]) {
      control.addEventListener("change", () => scheduleUpdate());
    }

    density.addEventListener("change", () => {
      sidebar.classList.toggle("compact", density.value === "compact");
      try {
        window.localStorage.setItem("teams-exporter-density", density.value);
      } catch (_) {
        // Some browsers disable storage for local files; density still works for this page.
      }
    });

    try {
      const savedDensity = window.localStorage.getItem("teams-exporter-density");
      if (savedDensity === "compact") {
        density.value = savedDensity;
        sidebar.classList.add("compact");
      }
    } catch (_) {
      // Ignore storage restrictions on file:// pages.
    }

    reset.addEventListener("click", () => {
      search.value = "";
      sort.value = "messages";
      messageFilter.value = "all";
      personFilter.value = "";
      typeFilter.value = "all";
      scheduleUpdate();
      search.focus();
    });

    loadMore.addEventListener("click", () => {
      visibleLimit += CONVERSATION_PAGE_SIZE;
      scheduleUpdate(false);
    });

    update();
  }

  function initializeMessages() {
    const scroller = document.querySelector("[data-message-scroller]");
    const batches = document.querySelector("[data-message-batches]");
    const loader = document.querySelector("[data-message-loader]");
    const button = document.querySelector("[data-load-older-messages]");
    if (!scroller || !batches || !loader || !button) return;

    window.requestAnimationFrame(() => {
      scroller.scrollTop = scroller.scrollHeight;
    });

    button.addEventListener("click", () => {
      const chunk = button.dataset.nextChunk;
      if (!chunk || button.disabled) return;

      button.disabled = true;
      button.textContent = "Loading…";
      const script = document.createElement("script");
      script.src = `messages/chunk-${chunk}.js`;

      receiveMessageChunk = (payload) => {
        const oldHeight = scroller.scrollHeight;
        const existingFirst = batches.firstElementChild;
        const template = document.createElement("template");
        template.innerHTML = payload.html.trim();
        const batch = template.content.firstElementChild;

        if (!batch) {
          script.onerror();
          return;
        }

        if (existingFirst && batch.dataset.endDay === existingFirst.dataset.startDay) {
          const duplicateSeparator = existingFirst.querySelector(".day-sep");
          if (duplicateSeparator?.dataset.day === batch.dataset.endDay) {
            duplicateSeparator.remove();
          }
        }

        batches.prepend(batch);
        scroller.scrollTop += scroller.scrollHeight - oldHeight;
        button.dataset.nextChunk = payload.nextChunk || "";
        button.disabled = false;
        button.textContent = "Load earlier messages";
        loader.querySelector("[data-remaining-messages]").textContent =
          `${payload.remaining} earlier message(s)`;
        loader.hidden = payload.nextChunk === null;
        receiveMessageChunk = null;
        script.remove();
      };

      script.onerror = () => {
        button.disabled = false;
        button.textContent = "Retry loading earlier messages";
        receiveMessageChunk = null;
        script.remove();
      };
      document.head.append(script);
    });
  }

  function initializeConversationNavigation() {
    const scroller = document.querySelector("[data-message-scroller]");
    const topButton = document.querySelector("[data-scroll-messages-top]");
    const bottomButton = document.querySelector("[data-scroll-messages-bottom]");
    if (!scroller || !topButton || !bottomButton) return;

    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth";
    const updateButtons = () => {
      topButton.disabled = scroller.scrollTop <= 1;
      bottomButton.disabled =
        scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 1;
    };
    topButton.addEventListener("click", () => scroller.scrollTo({ top: 0, behavior }));
    bottomButton.addEventListener("click", () =>
      scroller.scrollTo({ top: scroller.scrollHeight, behavior })
    );
    scroller.addEventListener("scroll", updateButtons, { passive: true });
    window.addEventListener("resize", updateButtons);
    window.requestAnimationFrame(updateButtons);
  }

  initializeSidebar();
  initializeMessages();
  initializeConversationNavigation();
})();
