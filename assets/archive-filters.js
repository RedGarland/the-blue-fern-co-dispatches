(() => {
  "use strict";
  const archive = document.querySelector(".archive--gaza");
  if (!archive) return;
  const records = Array.from(archive.querySelectorAll(".archive-record"));
  const groups = Array.from(archive.querySelectorAll(".archive-month"));
  const filters = Array.from(archive.querySelectorAll("[data-filter]"));
  const monthSelect = archive.querySelector("#archive-month");
  const result = archive.querySelector("#archive-result");
  if (!monthSelect || !result) return;
  const counts = records.reduce((map, record) => {
    const kind = record.dataset.recordType || "briefing";
    map[kind] = (map[kind] || 0) + 1;
    return map;
  }, {});
  let activeType = "briefing";
  let activeMonth = "";

  const months = Array.from(new Set(records.map((record) => record.dataset.month).filter(Boolean))).sort().reverse();
  months.forEach((month) => {
    const option = document.createElement("option");
    option.value = month;
    option.textContent = new Intl.DateTimeFormat("en", { month: "long", year: "numeric", timeZone: "UTC" })
      .format(new Date(`${month}-01T00:00:00Z`));
    monthSelect.appendChild(option);
  });

  const labels = { briefing: "Briefings", correction: "Corrections", monitoring: "Monitoring", all: "All records" };
  filters.forEach((button) => {
    const kind = button.dataset.filter || "briefing";
    button.textContent = `${labels[kind]} ${kind === "all" ? records.length : (counts[kind] || 0)}`;
    button.addEventListener("click", () => {
      activeType = kind;
      filters.forEach((candidate) => {
        const selected = candidate === button;
        candidate.classList.toggle("is-active", selected);
        candidate.setAttribute("aria-pressed", String(selected));
      });
      apply();
    });
  });
  monthSelect.addEventListener("change", () => {
    activeMonth = monthSelect.value;
    apply();
  });

  function apply() {
    let shown = 0;
    records.forEach((record) => {
      const typeMatches = activeType === "all" || record.dataset.recordType === activeType;
      const monthMatches = !activeMonth || record.dataset.month === activeMonth;
      const visible = typeMatches && monthMatches;
      record.hidden = !visible;
      if (visible) shown += 1;
    });
    groups.forEach((group) => {
      group.hidden = !group.querySelector(".archive-record:not([hidden])");
    });
    result.textContent = `${shown} record${shown === 1 ? "" : "s"} shown`;
  }
  apply();
})();

