"use strict";

const readline = require("readline");
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const Module = require("module");

// =====================================================
// isolated npm environment
// =====================================================

const NPM_ROOT = "/tmp/humaneval_node_env";

const NODE_MODULES = path.join(
  NPM_ROOT,
  "node_modules"
);

if (!fs.existsSync(NPM_ROOT)) {

  fs.mkdirSync(NPM_ROOT, {
    recursive: true,
  });

  execSync(
    "npm init -y",
    {
      cwd: NPM_ROOT,
      stdio: "ignore",
    }
  );
}

// require path 추가
process.env.NODE_PATH = NODE_MODULES;

Module._initPaths();

// =====================================================
// builtin modules
// =====================================================

const BUILTIN_MODULES = new Set([
  "fs",
  "path",
  "os",
  "util",
  "crypto",
  "stream",
  "http",
  "https",
  "url",
  "zlib",
  "events",
  "assert",
  "buffer",
  "timers",
  "querystring",
]);

// =====================================================
// optional whitelist
// =====================================================

// null이면 전체 허용
const ALLOWED_PACKAGES = null;

/*
예시:

const ALLOWED_PACKAGES = new Set([
  "lodash",
  "mathjs",
  "axios",
  "moment",
]);
*/

// =====================================================
// installed cache
// =====================================================

const installedPackages = new Set();

// =====================================================
// readline
// =====================================================

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false,
});

// =====================================================
// preprocess
// =====================================================

function preprocess(code) {

  return code
    .replace(/const\s+/g, "var ")
    .replace(/let\s+/g, "var ")
    .replace(/export\s+default\s+/g, "")
    .replace(/export\s+/g, "");
}

// =====================================================
// extract packages
// =====================================================

function extractPackages(code) {

  const packages = new Set();

  // require(...)
  const requireRegex =
    /require\s*\(\s*['"]([^'"]+)['"]\s*\)/g;

  // import ... from ...
  const importRegex =
    /from\s+['"]([^'"]+)['"]/g;

  let match;

  while ((match = requireRegex.exec(code)) !== null) {

    const pkg = match[1];

    if (
      !pkg.startsWith(".") &&
      !pkg.startsWith("/")
    ) {

      packages.add(
        pkg.split("/")[0]
      );
    }
  }

  while ((match = importRegex.exec(code)) !== null) {

    const pkg = match[1];

    if (
      !pkg.startsWith(".") &&
      !pkg.startsWith("/")
    ) {

      packages.add(
        pkg.split("/")[0]
      );
    }
  }

  return [...packages];
}

// =====================================================
// ensure packages
// =====================================================

function ensurePackages(packages) {

  for (const pkg of packages) {

    // builtin skip
    if (BUILTIN_MODULES.has(pkg)) {
      continue;
    }

    // whitelist
    if (
      ALLOWED_PACKAGES &&
      !ALLOWED_PACKAGES.has(pkg)
    ) {

      console.error(
        `[WARN] blocked package: ${pkg}`
      );

      continue;
    }

    // cache
    if (installedPackages.has(pkg)) {
      continue;
    }

    try {

      require.resolve(pkg);

      installedPackages.add(pkg);

    } catch {

      try {

        console.error(
          `[INFO] installing ${pkg}`
        );

        execSync(
          `npm install ${pkg} --silent --no-progress`,
          {
            cwd: NPM_ROOT,
            stdio: "ignore",
            timeout: 15000,
          }
        );

        installedPackages.add(pkg);

      } catch (e) {

        console.error(
          `[WARN] failed install: ${pkg}`
        );
      }
    }
  }
}

// =====================================================
// timeout wrapper
// =====================================================

function runWithTimeout(
  fn,
  args,
  timeoutMs = 5000
) {

  return Promise.race([

    Promise.resolve().then(() => {

      if (Array.isArray(args)) {
        return fn(...args);
      }

      return fn(args);
    }),

    new Promise((_, reject) =>
      setTimeout(
        () => reject(new Error("Timeout")),
        timeoutMs
      )
    ),
  ]);
}

// =====================================================
// execute code
// =====================================================

async function runCode(
  code,
  funcName,
  args
) {

  try {

    // ---------------------------------
    // preprocess
    // ---------------------------------

    const safeCode = preprocess(code);

    // ---------------------------------
    // auto install packages
    // ---------------------------------

    const packages =
      extractPackages(code);

    ensurePackages(packages);

    // ---------------------------------
    // wrapped execution
    // ---------------------------------

    const wrapped = `

      ${safeCode}

      let target = null;

      // global function
      if (
        typeof ${funcName} === "function"
      ) {

        target = ${funcName};
      }

      // module.exports = function ...
      else if (
        module &&
        module.exports &&
        typeof module.exports === "function"
      ) {

        target = module.exports;
      }

      // module.exports.foo = ...
      else if (
        module &&
        module.exports &&
        typeof module.exports["${funcName}"] === "function"
      ) {

        target =
          module.exports["${funcName}"];
      }

      // exports.foo = ...
      else if (
        exports &&
        typeof exports["${funcName}"] === "function"
      ) {

        target =
          exports["${funcName}"];
      }

      if (!target) {

        throw new Error(
          "Function not found: ${funcName}"
        );
      }

      return target;
    `;

    // ---------------------------------
    // CommonJS emulation
    // ---------------------------------

    const moduleObj = {
      exports: {},
    };

    const exportsObj =
      moduleObj.exports;

    const fn = new Function(
      "require",
      "module",
      "exports",
      wrapped
    )(
      require,
      moduleObj,
      exportsObj
    );

    // ---------------------------------
    // execute
    // ---------------------------------

    const result =
      await runWithTimeout(
        fn,
        args,
        1000
      );

    return {
      ok: true,
      result,
    };

  } catch (e) {

    return {
      ok: false,
      error: e.toString(),
    };
  }
}

// =====================================================
// stdin loop
// =====================================================

rl.on("line", async (line) => {

  try {

    const req = JSON.parse(line);

    const res = await runCode(
      req.code,
      req.func_name,
      req.args
    );

    console.log(
      JSON.stringify(res)
    );

  } catch (e) {

    console.log(
      JSON.stringify({
        ok: false,
        error: e.toString(),
      })
    );
  }
});