const historySections = [
  ["recurring_topics", "Повторяющиеся темы"],
  ["positive_changes", "Положительные изменения"],
  ["unresolved_questions", "Нерешённые вопросы"],
  ["signals_to_verify", "Сигналы для проверки"],
];
const missingEvidence = "Источник не указан: гипотеза или недостаточно данных";
const truncatedHistory = "Часть истории не вошла в контекст модели из-за ограничений размера.";

function escapeHtml(value) {
  const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(value ?? "").replace(/[&<>"']/g, (character) => entities[character]);
}

function jiraBrowseUrl(jiraBaseUrl, issueKey, commentId = null) {
  if (!jiraBaseUrl || !issueKey) return null;
  try {
    const url = new URL(jiraBaseUrl);
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    url.pathname = `${url.pathname.replace(/\/$/, '')}/browse/${encodeURIComponent(issueKey)}`;
    url.search = '';
    url.hash = '';
    if (commentId) {
      url.searchParams.set('focusedCommentId', commentId);
      url.searchParams.set(
        'page',
        'com.atlassian.jira.plugin.system.issuetabpanels:comment-tabpanel',
      );
      url.hash = `comment-${encodeURIComponent(commentId)}`;
    }
    return url.toString();
  } catch {
    return null;
  }
}

export function sourceDescriptor(sourceRef, sourceContext = {}) {
  const value = String(sourceRef || '');
  if (/^SRC-[0-9]{4,}$/.test(value)) {
    return { label: 'Источник Jira не восстановлен', url: null };
  }
  if (value.startsWith('jira-comment:')) {
    const commentId = value.slice('jira-comment:'.length);
    return {
      label: `Комментарий ${commentId}`,
      url: jiraBrowseUrl(sourceContext.jiraBaseUrl, sourceContext.issueKey, commentId),
    };
  }
  if (value.startsWith('jira:')) {
    const issueKey = value.slice('jira:'.length);
    return {
      label: `Задача ${issueKey}`,
      url: jiraBrowseUrl(sourceContext.jiraBaseUrl, issueKey),
    };
  }
  return { label: value || 'Источник Jira не указан', url: null };
}

function evidenceText(items, sourceContext) {
  if (!items?.length) return missingEvidence;
  const sources = items.map(({ date, source_ref }) => {
    const source = sourceDescriptor(source_ref, sourceContext);
    const reference = source.url ? `${source.label} — ${source.url}` : source.label;
    return [date, reference].filter(Boolean).join(' · ');
  });
  return `Источники: ${sources.join("; ")}`;
}

function markdownList(items) {
  return items.map((item) => `- ${item}`);
}

export function toEditableText(result, sourceContext = {}) {
  const history = result.history_summary;
  const cheatSheet = result.curator_cheat_sheet;
  const lines = [
    "# Сценарий встречи 1-to-1", "", "## Краткий анализ истории", "",
    history.overall_dynamics || "Недостаточно данных для оценки динамики.",
  ];

  for (const [key, title] of historySections) {
    if (!history[key].length) continue;
    lines.push("", `### ${title}`);
    for (const item of history[key]) {
      lines.push(`- ${item.statement}`);
      if (item.verify_question) lines.push(`  Вопрос для проверки: ${item.verify_question}`);
      lines.push(`  ${evidenceText(item.evidence, sourceContext)}`);
    }
  }

  lines.push(
    "", "## Идеи для начала разговора", "",
    ...markdownList(result.conversation_openers.map((item) => item.phrase)),
    "", "## Приоритеты встречи", "",
  );
  for (const item of result.meeting_priorities) {
    lines.push(
      `### ${item.priority}. ${item.topic}`,
      item.why_now || '',
      evidenceText(item.evidence, sourceContext),
      '',
    );
  }

  lines.push("## Осторожные формулировки", "");
  for (const item of result.sensitive_topics) {
    lines.push(
      `### ${item.topic}`, `Не использовать: ${item.avoid}`, `Лучше сказать: ${item.use_instead}`,
      `Когда поднять тему: ${item.when_to_raise}`, "",
    );
  }

  lines.push("## Скрипт 30-минутной встречи", "");
  for (const slot of result.agenda) {
    lines.push(`### ${slot.start_minute}–${slot.end_minute} мин. — ${slot.goal}`);
    if (slot.curator_lines.length) {
      lines.push("Реплики куратора:", ...markdownList(slot.curator_lines));
    }
    if (slot.key_questions.length) {
      lines.push("Вопросы:");
      for (const question of slot.key_questions) {
        lines.push(`- ${question.question}`);
        if (question.follow_up) lines.push(`  Уточнение: ${question.follow_up}`);
      }
    }
    if (slot.listen_for.length) {
      lines.push("На что обратить внимание:", ...markdownList(slot.listen_for));
    }
    lines.push("");
  }

  const agreements = cheatSheet.suggested_agreements.map(({ action, owner, due }) =>
    `${action} · владелец: ${owner} · срок: ${due}`);
  lines.push(
    "## Памятка куратору", "", "### Три сигнала", ...markdownList(cheatSheet.three_signals),
    "", "### Три желаемых исхода", ...markdownList(cheatSheet.three_desired_outcomes),
    "", "### Проекты договорённостей", ...markdownList(agreements),
    "", "### Сообщение после встречи", "", cheatSheet.follow_up_message_template || "",
  );
  const quality = result.data_quality;
  if (quality.context_cut || quality.warnings.length) {
    lines.push("", "## Качество данных", "");
    if (quality.context_cut) lines.push(`- ${truncatedHistory}`);
    lines.push(...markdownList(quality.warnings));
  }
  return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
}

function evidenceHtml(items, sourceContext) {
  if (!items?.length) return `<small>${missingEvidence}</small>`;
  const sources = items.map(({ date, source_ref }) => {
    const source = sourceDescriptor(source_ref, sourceContext);
    const reference = source.url
      ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.label)}</a>`
      : escapeHtml(source.label);
    return `<div>${escapeHtml(date)} · ${reference}</div>`;
  });
  return `<details class="evidence"><summary>Источники (${items.length})</summary>
    ${sources.join("")}</details>`;
}

function listHtml(items) {
  return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function historyHtml(history, sourceContext) {
  const statements = historySections.flatMap(([key]) => history[key]);
  const articles = statements.map((item) => `<article>
    <b>${escapeHtml(item.statement)}</b><p>${escapeHtml(item.verify_question)}</p>
    ${evidenceHtml(item.evidence, sourceContext)}
  </article>`);
  return `<h3>Краткий анализ истории</h3><p>${escapeHtml(history.overall_dynamics)}</p>
    ${articles.join("")}`;
}

function agendaHtml(agenda) {
  const rows = agenda.map((slot) => {
    const questions = slot.key_questions.map(({ question, follow_up }) => {
      const followUp = follow_up ? `<br><small>↳ ${escapeHtml(follow_up)}</small>` : "";
      return escapeHtml(question) + followUp;
    });
    return `<tr>
      <td>${escapeHtml(slot.start_minute)}–${escapeHtml(slot.end_minute)}</td>
      <td>${escapeHtml(slot.goal)}</td>
      <td>${slot.curator_lines.map(escapeHtml).join("<br>")}</td>
      <td>${questions.join("<br>")}</td>
      <td>${slot.listen_for.map(escapeHtml).join("<br>")}</td>
    </tr>`;
  });
  return `<h3>Скрипт 30-минутной встречи</h3>
    <table class="agenda">
      <thead><tr><th>Время</th><th>Цель</th><th>Реплики</th><th>Вопросы</th><th>Слушать</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table>`;
}

function renderStructuredResult(result, sourceContext) {
  const priorities = result.meeting_priorities.map((item) => `<article>
    <b>${escapeHtml(item.priority)}. ${escapeHtml(item.topic)}</b>
    <p>${escapeHtml(item.why_now)}</p>${evidenceHtml(item.evidence, sourceContext)}
  </article>`);
  const sensitiveTopics = result.sensitive_topics.map((item) => `<article>
    <b>${escapeHtml(item.topic)}</b>
    <p><s>${escapeHtml(item.avoid)}</s><br>${escapeHtml(item.use_instead)}</p>
    <small>${escapeHtml(item.when_to_raise)}</small>
  </article>`);

  const cheatSheet = result.curator_cheat_sheet;
  const agreements = cheatSheet.suggested_agreements.map(({ action, owner, due }) =>
    `${action} · владелец: ${owner} · срок: ${due}`);
  const quality = result.data_quality;
  const qualityHtml = quality.context_cut || quality.warnings.length
    ? `<h4>Качество данных</h4>
      ${quality.context_cut ? `<p class="warning">${truncatedHistory}</p>` : ""}
      ${listHtml(quality.warnings)}`
    : "";

  return `
    ${historyHtml(result.history_summary, sourceContext)}
    <h3>Идеи для начала разговора</h3>
    ${listHtml(result.conversation_openers.map((item) => item.phrase))}
    <h3>Приоритеты встречи</h3>${priorities.join("")}
    <h3>Осторожные формулировки</h3>${sensitiveTopics.join("")}
    ${agendaHtml(result.agenda)}
    <h3>Памятка куратору</h3>
    <h4>Три сигнала</h4>${listHtml(cheatSheet.three_signals)}
    <h4>Три желаемых исхода</h4>${listHtml(cheatSheet.three_desired_outcomes)}
    <h4>Проекты договорённостей</h4>${listHtml(agreements)}
    <h4>Сообщение после встречи</h4><p>${escapeHtml(cheatSheet.follow_up_message_template)}</p>
    ${qualityHtml}`;
}

export function renderResult(result, sourceContext = {}) {
  document.querySelector("#editable-result").value = toEditableText(result, sourceContext);
  document.querySelector("#rendered-result").innerHTML = renderStructuredResult(
    result,
    sourceContext,
  );
}
