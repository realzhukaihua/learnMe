'use strict';

// UI text in two languages. Strings are written in English in the code and looked up here by
// their English text (a missing entry shows the English). The language comes from the server
// (/lang.js, set by `language` in learnme.toml or LEARNME_LANG).

const LANG = window.LEARNME_LANG === 'zh' ? 'zh' : 'en';

const ZH = {
  // statuses and labels
  'Running': '运行中', 'Waiting for you': '等你确认', 'Turn done · needs a decision': '本轮完成 · 待决策',
  'Interrupted · needs a decision': '已中断 · 待决策', 'Error · needs a decision': '出错 · 待决策', 'Ended': '已结束',
  'Working': '处理中', 'Online': '在线', 'Interrupted': '已中断', 'Error': '出错',
  'Task': '任务', 'Append': '追加', 'Correct': '纠正', 'Continue': '继续', 'Reopen': '重新打开', 'History': '历史',
  'Goal reached': '目标已达成', 'Wrong direction': '方向不对', 'Poor results': '效果不好',
  'Not worth continuing': '不值得继续', 'Parking it': '先放一放', 'Duplicate / done elsewhere': '重复 / 已在别处做',
  'Open': '打开', 'Not ended': '未结束', 'Needs me': '需要我', 'All': '全部', 'Needs a decision': '待你决策',
  'List projects': '查看项目', 'Dispatch a task': '派发子任务', 'List tasks': '查看任务列表', 'Check a task': '查看任务进度',
  'Message a task': '给任务发消息', 'Interrupt a task': '中断任务', 'End a task': '结束任务',
  'just now': '刚刚', '{n} min ago': '{n} 分钟前', '{n} h ago': '{n} 小时前', '{n} d ago': '{n} 天前',
  ' (correction)': '（纠正）', 'before': '原文', 'after': '改为',
  // attachments
  'Attach files (or drag them in, or paste a screenshot; up to {mb}MB each)': '添加附件（也可以把文件拖进来、粘贴截图；单个不超过 {mb}MB）',
  'Over the {mb}MB limit ({size})': '超过 {mb}MB 上限（{size}）', 'Network error or connection lost': '网络错误或连接被断开',
  'Upload of "{name}" failed: {why}': '「{name}」上传失败：{why}', 'Failed: {err}': '失败：{err}', 'Remove': '移除',
  'Attachments are still uploading; send when they finish': '附件还在上传，传完再发送',
  'An attachment failed to upload; remove it with × before sending': '有附件上传失败，点 × 移除后再发送',
  'Attachments:': '附件：',
  // the event log
  'Failed to load the window: {msg}': '加载窗口失败：{msg}', 'Manager': '主理人', 'Manager · auto-supervision': '主理人 · 自动监督',
  'Auto-supervision': '自动监督', 'System': '系统', 'Outside input': '外部输入', 'You': '你',
  'permission {mode}': '权限 {mode}', '💭 Thinking': '💭 思考', 'Open in Slack': '在 Slack 打开',
  '🗜 Context nearly full; compacted automatically': '🗜 上下文快满了，已自动总结压缩', '🗜 Context compacted': '🗜 已总结压缩上下文',
  '{n} tokens before': '压缩前 {n} token', 'took {d}': '用时 {d}', 'View the summary': '查看压缩摘要',
  'Memory created': '新增了记忆', 'Memory deleted': '删除了记忆', 'Memory updated': '更新了记忆', 'Undo': '撤销', 'Details': '详情',
  '💭 Thinking… {n} chars': '💭 思考中… {n} 字', '☑ Updated the task list': '☑ 更新了任务清单', '(no output)': '（无输出）',
  'Result': '结果', 'Open window': '打开窗口',
  // permission cards
  '❓ The agent has a question for you': '❓ Agent 有问题要问你', 'Other (type your own)': '其他（自己填写）',
  'Skip and explain instead…': '不回答，直接给说明…', 'Submit': '提交回答', 'Every question needs an answer': '每个问题都需要一个回答',
  "Don't answer": '不回答', '📋 The agent proposed a plan for your approval': '📋 Agent 提交了计划，等你批准',
  'The agent wants to use {tool}': 'Agent 想使用 {tool}', 'Feedback (sent to the agent if you reject)': '修改意见（拒绝时发给 Agent）',
  'Reason (optional)': '拒绝理由（可选）', 'Approve plan': '批准计划', 'Allow': '允许', 'Always allow': '总是允许', 'Deny': '拒绝',
  '✓ Allowed': '✓ 已允许', '✓ Always allowed': '✓ 已设为总是允许', '✗ Denied': '✗ 已拒绝', '✓ Answered': '✓ 已回答',
  '— Cancelled': '— 已取消', '— Expired': '— 已失效',
  // results and header
  '✓ Compacted': '✓ 压缩完成', '✓ Turn done': '✓ 本轮完成', '⏹ Interrupted': '⏹ 已中断', '✗ Error': '✗ 出错',
  '{n} steps': '{n} 步', 'total ${cost}': '累计 ${cost}', 'All done': '全部完成',
  'Main window · manager': '主窗口 · 主理人', "Talk to me here; I'll open windows for independent work": '在这里和我说话，独立的事我会开新窗口',
  'Send': '发送',
  'Enter appends to the running task · Ctrl+Enter interrupts and corrects · Esc interrupts': 'Enter 追加给正在运行的任务 · Ctrl+Enter 中断并纠正 · Esc 中断',
  'Talk to the manager… (Enter to send · Shift+Enter for a new line)': '和主理人说话…（Enter 发送 · Shift+Enter 换行）',
  'The task has ended; a message reopens it': '任务已结束；发消息会重新打开它',
  'Continue or give a new direction… (Enter to send · Shift+Enter for a new line)': '继续对话或给出新方向…（Enter 发送 · Shift+Enter 换行）',
  'Context: {n} tokens': '上下文已用 {n} token', ' (window size known once the session connects)': '（满窗大小要等会话连上后才知道）',
  ' of {w} ({pct}%). It compacts automatically when nearly full, or click "Compact"': '，满窗 {w}（{pct}%）。快满时会自动压缩，也可以点「总结压缩」',
  'Clear for sure?': '确认清空？', 'Clear': '清空对话',
  'Write the new direction first, then "Interrupt & correct"': '先写下新的方向，再「中断并纠正」',
  'Send failed: {msg}': '发送失败：{msg}', '↑ Needs you: {title}': '↑ 需要你看：{title}',
  // sidebar and connection
  'Nothing waiting for you': '没有等你处理的任务', 'No tasks yet': '这里还没有任务', 'Dispatched by the manager': '主理人派发',
  'Dispatched for a Slack request': '按 Slack 请求派发', 'Opened by outside input': '外部输入打开的',
  'Main window': '主窗口', '● Connected': '● 已连接', '● Reconnecting…': '● 重连中…', '● Disconnected': '● 未连接',
  'Failed to load state: {msg}': '加载状态失败：{msg}', 'Failed to load projects: {msg}': '读取项目列表失败：{msg}',
  '{name} · used {n}×': '{name} · 用过 {n} 次', 'Dispatch failed: {msg}': '派发失败：{msg}',
  // memory
  'External': '外部', 'Reverted this memory change': '已回滚这次记忆修改', '🧠 Memory updated: {where} ({by})': '🧠 记忆已更新：{where}（{by}）',
  'Always-loaded memory needs layering: ': '常驻部分该分层：', 'Failed to load memory: {msg}': '读取记忆失败：{msg}',
  '{n} chars total': '共 {n} 字', '{n} chars always loaded': '常驻 {n} 字', '{n} orphans': '孤儿 {n}',
  'Memory files in no index': '没进任何索引的记忆文件', '{n} dead links': '死链 {n}', '{n} possibly stale': '可能过时 {n}',
  'In-progress-type entries not updated for 30+ days': '超过 30 天没更新的「进行中/待合」类条目', 'Layer': '分层整理',
  'Dispatch a task into this project: reorganize and archive stale state, delete no knowledge': '派一个任务到该项目：只重组、归档过时状态，不删任何知识',
  'Dispatched a layering task': '已派出分层整理任务', '{n} files': '{n} 个', '"{q}": {n} hits': '「{q}」找到 {n} 处',
  'Nothing found': '没有找到', 'Search failed: {msg}': '检索失败：{msg}', 'Pick a memory file on the left': '选择左边的一份记忆',
  'Cancel': '取消', 'Save': '保存', 'Saved; this edit is remembered as your correction': '已保存；这次修改会作为你的纠正被记住',
  'Edit': '编辑', 'No memory changes yet': '还没有记忆变更', 'Created': '新增', 'Deleted': '删除', 'Modified': '修改', 'Revert': '回滚',
  // history
  'Terminal': '终端', 'Today': '今天', 'Yesterday': '昨天', 'This week': '本周', 'Earlier': '更早',
  'Scanning Claude Code sessions…': '扫描 Claude Code 会话…', 'Loading…': '加载中…', 'Failed to load history: {msg}': '读取历史失败：{msg}',
  '{n} items': '{n} 条', 'Ended: {reason}': '结束原因：{reason}', '{n} prompts': '{n} 条提问', 'Last: {text}': '最后：{text}',
  'Restore as window': '恢复到窗口', 'Restored: your next message continues from the original context': '已恢复：接着发消息就从原来的上下文继续',
  'No history yet': '还没有历史', 'Auto-supervision on': '自动监督已开启', 'Auto-supervision off': '自动监督已关闭',
  // backends
  'Default': '默认', 'Backend': '后端', 'Manager on': '主理人后端', 'unavailable': '不可用',
  'The manager now runs on {name}': '主理人已切换到 {name}',
  // static page (index.html)
  'Task list': '任务列表', 'Auto-supervise': '自动监督', 'Demo · scripted agents': '演示 · 剧本模式',
  "Demo mode: the agents' replies are scripted and no model is called; the console around them is the real code. Run ./run.sh demo again to replay.": '演示模式：agent 的回复来自剧本，不调用任何模型；外面的控制台是真实代码。再跑一次 ./run.sh demo 可以重播。',
  'When a task stops, the manager reviews it and continues, corrects or ends it; when it ends, memory and your judgment habits are updated':
    '子任务停下来时，主理人自动验收、推进或纠正；任务结束时自动更新记忆和你的判断习惯',
  '🕘 History': '🕘 历史', 'History: learnMe windows and every Claude Code session on this machine': '历史：learnMe 的窗口，以及这台机器上所有 Claude Code 会话',
  '🧠 Memory': '🧠 记忆', "Memory: your profile, the manager's memory, each project's memory and the change history": '记忆：你的画像、主理人记忆、各项目记忆和变更历史',
  '＋ New task': '＋ 直接派发', 'Connection': '连接状态', 'No task windows open yet.': '还没有打开的任务窗口。',
  'Tell the manager in the main window to "go to <b>some project</b> and do <b>something</b>" and it opens a window; or click "New task" at the top right.':
    '在主窗口里说「去 <b>某项目</b> 做 <b>某件事</b>」，主理人会开一个子窗口；也可以点右上角「直接派发」。',
  'Project folder': '项目目录', '/path/to/project, or type a name to filter': '/path/to/project 或输入名字筛选',
  'What to do, background, what counts as done…  (Ctrl+Enter to dispatch)': '要做什么、背景、怎样算完成…  (Ctrl+Enter 派发)',
  'Model': '模型', 'Permissions': '权限', 'Auto-accept edits': '自动接受编辑', 'Allow everything': '全部放行', 'Plan first': '先出计划',
  'Ask every time': '每次询问', 'Dispatch': '派发', 'Memory files': '记忆文件', 'Changes': '变更记录',
  'Search all memory (space-separated terms)': '检索全部记忆（空格分隔多个词）', 'Close': '关闭', 'learnMe windows': 'learnMe 窗口',
  'Claude Code sessions': 'Claude Code 会话', 'Search titles, content, projects (space-separated)': '搜索标题、内容、项目（空格分隔）',
  'Compact': '总结压缩', 'Summarize the conversation so far to free up context (like /compact)': '总结压缩：把前面的对话总结成一份摘要，腾出上下文（等同 /compact）',
  'Clear the conversation: the manager starts fresh and old messages leave the screen (they stay in the database and searchable). Click twice to confirm':
    '清空对话：主理人从头开始，屏幕上的旧消息清掉（仍保存在库里，可以搜索）。点两次确认',
  'Maximize / restore': '放大 / 还原', 'Open in a new browser window': '在新浏览器窗口打开', 'Hide the window (the task keeps running)': '收起窗口（任务不受影响）',
  'Why end this task?': '为什么结束这个任务？', 'More reasons (optional; used to learn your judgment)': '补充原因（可选，会用来学习你的判断）',
  'End task': '结束任务',
  'Summarize the conversation so far to free up context. Goals, progress and conclusions are kept; details of the process are lost.':
    '把前面的对话总结成一份摘要，腾出上下文。目标、进展和结论会保留，过程细节会丢。',
  'What to keep (optional), e.g. PR links, open problems': '重点保留什么（可空），比如：PR 链接、还没解决的问题',
  'Compact now': '开始压缩', 'Interrupt & correct': '中断并纠正', '⏹ Interrupt': '⏹ 中断',
  // fighter
  'Boxing': '拳法', 'Kicks': '腿法', 'Tai chi': '太极', 'Staff': '棍法', 'Sword': '剑法', 'At attention': '收势立正', 'Practising': '练功中',
};

function tr(text, vars) {
  let out = LANG === 'zh' && Object.prototype.hasOwnProperty.call(ZH, text) ? ZH[text] : text;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return out;
}

// Static markup: data-i18n (text), data-i18n-html, data-i18n-title, data-i18n-placeholder.
function applyI18n(root = document) {
  document.documentElement.lang = LANG === 'zh' ? 'zh-CN' : 'en';
  const each = (sel, fn) => root.querySelectorAll(sel).forEach(fn);
  each('[data-i18n]', el => { el.textContent = tr(el.dataset.i18n); });
  each('[data-i18n-html]', el => { el.innerHTML = tr(el.dataset.i18nHtml); });
  each('[data-i18n-title]', el => { el.title = tr(el.dataset.i18nTitle); });
  each('[data-i18n-placeholder]', el => { el.placeholder = tr(el.dataset.i18nPlaceholder); });
  if (root === document) document.querySelectorAll('template').forEach(t => applyI18n(t.content));
}
