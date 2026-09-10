// docs/index.html の判定ロジック（<script id="engine">）を Node.js で実行し、
// 学生の射影・質問への回答・通知をJSONで標準出力へ書き出す。
// tests/test_docs_mirror.py が、この出力を Python 実装の結果と突き合わせる。
//
//   node tests/js/check_mirror.mjs <docs/index.html> <questions.json>

import { readFileSync } from "node:fs";
import vm from "node:vm";

const [htmlPath, questionsPath] = process.argv.slice(2);
const html = readFileSync(htmlPath, "utf8");
const questions = JSON.parse(readFileSync(questionsPath, "utf8"));

const blocks = new Map();
for (const match of html.matchAll(/<script[^>]*\bid="([a-z-]+)"[^>]*>([\s\S]*?)<\/script>/g)) {
  blocks.set(match[1], match[2]);
}

const engine = blocks.get("engine");
if (!engine) {
  console.error('docs/index.html に <script id="engine"> がありません。');
  process.exit(1);
}

globalThis.document = {
  getElementById: (id) => (blocks.has(id) ? { textContent: blocks.get(id) } : null),
};

const probe = `
;(() => {
  const forStudent = (target) => ({
    projection: {
      student_id: target.student_id,
      name: target.name,
      faculty: target.faculty,
      year_of_study: target.year_of_study,
      earned_credits: target.earned_credits,
      current_registered_credits: target.current_registered_credits,
      completed_courses: target.completed_courses,
      registered_courses: target.registered_courses,
      registration_completed: target.registration_completed,
    },
    answers: ${JSON.stringify(questions)}.map((question) => {
      const { intent, decision } = decide(question, target);
      return {
        question,
        intent,
        status: decision.status,
        message: decision.message,
        unmet: decision.unmet,
        terms: decision.terms,
        horn_clause: decision.hornClause,
      };
    }),
    notifications: notificationsFor(target).map((notice) => ({
      level: notice.level, title: notice.title, message: notice.message,
    })),
  });
  return Object.fromEntries(STUDENTS.map((target) => [target.student_id, forStudent(target)]));
})()
`;

const output = vm.runInThisContext(engine + probe, { filename: "docs/index.html#engine" });
process.stdout.write(JSON.stringify(output, null, 1));
