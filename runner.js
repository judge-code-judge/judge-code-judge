// "use strict";

// const readline = require("readline");

// const rl = readline.createInterface({
//   input: process.stdin,
//   output: process.stdout,
//   terminal: false,
// });

// function preprocess(code) {
//   // 🔥 const / let → var 변환
//   return code
//     .replace(/const\s+/g, "var ")
//     .replace(/let\s+/g, "var ");
// }

// async function runCode(code, funcName, args) {
//   try {
//     const safeCode = preprocess(code);

//     const wrapped = `
//       ${safeCode}
//       return ${funcName};
//     `;

//     const fn = new Function(wrapped)();

//     let result;
//     if (Array.isArray(args)) {
//       result = fn(...args);
//     } else {
//       result = fn(args);
//     }

//     return { ok: true, result };
//   } catch (e) {
//     return { ok: false, error: e.toString() };
//   }
// }

// rl.on("line", async (line) => {
//   try {
//     const req = JSON.parse(line);
//     const res = await runCode(req.code, req.func_name, req.args);
//     console.log(JSON.stringify(res));
//   } catch (e) {
//     console.log(JSON.stringify({ ok: false, error: e.toString() }));
//   }
// });


"use strict";

const readline = require("readline");

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false,
});

// -----------------------------
// 전처리: const/let → var
// -----------------------------
function preprocess(code) {
  return code
    .replace(/const\s+/g, "var ")
    .replace(/let\s+/g, "var ");
}

// -----------------------------
// timeout wrapper
// -----------------------------
function runWithTimeout(fn, args, timeoutMs = 5000) {
  return Promise.race([
    Promise.resolve().then(() => {
      if (Array.isArray(args)) {
        return fn(...args);
      } else {
        return fn(args);
      }
    }),
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error("Timeout")), timeoutMs)
    ),
  ]);
}

// -----------------------------
// 코드 실행
// -----------------------------
async function runCode(code, funcName, args) {
  try {
    const safeCode = preprocess(code);

    const wrapped = `
      ${safeCode}
      if (typeof ${funcName} !== 'function') {
        throw new Error("Function not found: ${funcName}");
      }
      return ${funcName};
    `;

    const fn = new Function(wrapped)();

    const result = await runWithTimeout(fn, args, 1000);

    return { ok: true, result };
  } catch (e) {
    return { ok: false, error: e.toString() };
  }
}

// -----------------------------
// stdin 처리
// -----------------------------
rl.on("line", async (line) => {
  try {
    const req = JSON.parse(line);

    const res = await runCode(
      req.code,
      req.func_name,
      req.args
    );

    console.log(JSON.stringify(res));
  } catch (e) {
    console.log(JSON.stringify({ ok: false, error: e.toString() }));
  }
});