<?php
declare(strict_types=1);

error_reporting(E_ALL);
ini_set('display_errors', 'stderr');

if (!defined('STDIN')) define('STDIN', fopen('php://stdin', 'r'));
if (!defined('STDOUT')) define('STDOUT', fopen('php://stdout', 'w'));
if (!defined('STDERR')) define('STDERR', fopen('php://stderr', 'w'));

const MCP_PROTOCOL = '2025-03-26';
const MAX_READ_BYTES = 5242880;
const MAX_SEARCH_RESULTS = 100;
const EXEC_TIMEOUT_MAX = 7200.0;

$__IS_CLI = (PHP_SAPI === 'cli' || PHP_SAPI === 'php-cli');
$rootEnv = getenv('MCP_FS_ROOT') ?: '';
// Resolve the Linux home directory of the effective user running the script.
// Order: posix passwd entry (per euid) -> $HOME env -> $_SERVER['HOME'].
// Web default: user's home dir. CLI default: current working directory.
function resolveUserHomeDir(): ?string {
    $home = null;
    if (function_exists('posix_getpwuid') && function_exists('posix_geteuid')) {
        $pw = @posix_getpwuid(posix_geteuid());
        if (is_array($pw) && !empty($pw['dir']) && is_dir($pw['dir'])) $home = $pw['dir'];
    }
    if ($home === null) {
        $h = getenv('HOME');
        if (is_string($h) && $h !== '' && is_dir($h)) $home = $h;
    }
    if ($home === null && isset($_SERVER['HOME']) && is_dir($_SERVER['HOME'])) $home = $_SERVER['HOME'];
    return $home;
}
if ($rootEnv !== '') {
    $rootPath = $rootEnv;
} elseif ($__IS_CLI) {
    $rootPath = getcwd();
} else {
    $rootPath = resolveUserHomeDir() ?? dirname(__DIR__, 5);
}
$root = realpath($rootPath);
if ($root === false || !is_dir($root)) {
    if ($__IS_CLI) {
        fwrite(STDERR, "Invalid MCP_FS_ROOT\n");
        exit(1);
    }
    http_response_code(500);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'Invalid MCP_FS_ROOT']);
    exit(1);
}

// Allowed roots: primary $root plus POSIX scratch dirs and any extras from MCP_FS_EXTRA_ROOTS.
// Separators: ':' (unix path list), ',' or ';'. Missing/unresolvable entries are skipped silently.
$__extraRaw = getenv('MCP_FS_EXTRA_ROOTS');
$__extraList = ($__extraRaw === false || trim($__extraRaw) === '') ? '/tmp:/var/tmp' : $__extraRaw;
$allowedRoots = [$root];
foreach (preg_split('/[:,;]/', $__extraList) ?: [] as $seg) {
    $seg = trim($seg);
    if ($seg === '') continue;
    $rp = realpath($seg);
    if ($rp !== false && is_dir($rp) && !in_array($rp, $allowedRoots, true)) $allowedRoots[] = $rp;
}

function assertWithinAllowedRoots(string $resolved, string $displayPath): void {
    global $allowedRoots;
    foreach ($allowedRoots as $r) {
        if ($resolved === $r || str_starts_with($resolved, $r . '/')) return;
    }
    throw new RuntimeException("path escapes allowed roots [" . implode(', ', $allowedRoots) . "]: $displayPath");
}

function emit(array $value): void {
    echo json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE) . "\n";
    if (function_exists('flush')) flush();
}

function toolResult(mixed $value, bool $error = false): array {
    return ['content' => [['type' => 'text', 'text' => is_string($value) ? $value : json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT)]], 'isError' => $error];
}

function auditLog(string $level, string $message): void {
    global $root;
    $path = __DIR__ . '/mcp-fs.log';
    $line = date('Y-m-d H:i:s') . ' ' . strtoupper($level) . ' [audit] ' . str_replace(["\r", "\n"], ['\\r', '\\n'], $message) . PHP_EOL;
    @file_put_contents($path, $line, FILE_APPEND | LOCK_EX);
}

function auditUser(): string {
    $info = function_exists('posix_getpwuid') && function_exists('posix_geteuid') ? @posix_getpwuid(posix_geteuid()) : false;
    return is_array($info) && isset($info['name']) ? $info['name'] : (get_current_user() ?: '?');
}

function auditCompact(mixed $value): mixed {
    if (is_string($value)) return strlen($value) > 200 ? '<str ' . strlen($value) . 'b> ' . substr($value, 0, 200) . '…' : $value;
    if (is_array($value)) {
        // Recurse first so oversized leaves (e.g. write_file.content) are compacted individually,
        // keeping the associative dict structure visible in the log.
        foreach ($value as $key => $item) $value[$key] = auditCompact($item);
        // Only collapse the container when it's a flat list that is still too long after compaction.
        if (array_is_list($value) && strlen(serialize($value)) > 1024) {
            return '<array ' . count($value) . ' items, ' . strlen(serialize($value)) . 'b>';
        }
    }
    return $value;
}

function auditIsError(array $result): bool {
    return !empty($result['isError']) || !empty($result['is_error']);
}

function auditErrorText(array $result): ?string {
    return $result['content'][0]['text'] ?? null;
}

function basePath(string $cwd = '.'): string {
    global $root, $allowedRoots;
    $path = str_starts_with($cwd, '/') ? $cwd : $root . '/' . $cwd;
    $resolved = realpath($path);
    if ($resolved === false || !is_dir($resolved)) {
        throw new RuntimeException("working directory does not exist: $cwd");
    }
    assertWithinAllowedRoots($resolved, $cwd);
    return $resolved;
}

function resolvePath(string $path, string $cwd = '.', bool $mustExist = false): string {
    global $root;
    if ($path === '' || str_contains($path, "\0")) {
        throw new RuntimeException('path must not be empty');
    }
    $candidate = str_starts_with($path, '/') ? $path : basePath($cwd) . '/' . $path;
    $resolved = realpath($candidate);
    if ($resolved === false) {
        if ($mustExist) {
            throw new RuntimeException("path does not exist: $candidate");
        }
        $parent = realpath(dirname($candidate));
        if ($parent === false) {
            throw new RuntimeException("parent directory does not exist: $candidate");
        }
        $resolved = $parent . '/' . basename($candidate);
    }
    assertWithinAllowedRoots($resolved, $path);
    return $resolved;
}

function runCommand(array|string $command, string $cwd, float $timeout = EXEC_TIMEOUT_MAX): array {
    $spec = [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
    $process = @proc_open($command, $spec, $pipes, $cwd);
    if (!is_resource($process)) {
        $bin = is_array($command) ? ($command[0] ?? '?') : (string) $command;
        $hint = '';
        if (is_array($command) && isset($command[0]) && is_string($command[0])) {
            if ($command[0] !== '' && !str_contains($command[0], '/')) {
                $found = trim((string) @shell_exec('command -v ' . escapeshellarg($command[0]) . ' 2>/dev/null'));
                if ($found === '') $hint = " (binary not found in PATH: {$command[0]}; pass shell=true to use /bin/sh -c)";
            } elseif ($command[0] !== '' && !is_executable($command[0])) {
                $hint = " (binary not executable: {$command[0]})";
            }
        }
        throw new RuntimeException("could not start process [cwd={$cwd}]" . $hint);
    }
    fclose($pipes[0]);
    stream_set_blocking($pipes[1], false);
    stream_set_blocking($pipes[2], false);
    $stdout = $stderr = '';
    $deadline = microtime(true) + min(max(0.1, $timeout), EXEC_TIMEOUT_MAX);
    $timedOut = false;
    $exitCode = null;
    while (true) {
        $read = [];
        if (!feof($pipes[1])) $read[] = $pipes[1];
        if (!feof($pipes[2])) $read[] = $pipes[2];
        if ($read) {
            $write = $except = [];
            @stream_select($read, $write, $except, 1);
            foreach ($read as $stream) {
                $chunk = stream_get_contents($stream);
                if ($stream === $pipes[1]) $stdout .= $chunk;
                else $stderr .= $chunk;
            }
        }
        $status = proc_get_status($process);
        if (!$status['running'] && feof($pipes[1]) && feof($pipes[2])) { $exitCode = $status['exitcode']; break; }
        if (microtime(true) >= $deadline) {
            $timedOut = true;
            proc_terminate($process, 9);
            break;
        }
    }
    if ($timedOut) {
        $stdout .= stream_get_contents($pipes[1]);
        $stderr .= stream_get_contents($pipes[2]);
    }
    fclose($pipes[1]);
    fclose($pipes[2]);
    $exit = proc_close($process);
    return ['returncode' => $timedOut ? -9 : ($exitCode ?? $exit), 'stdout' => $stdout, 'stderr' => $stderr, 'timed_out' => $timedOut];
}

function cleanupPycache(?string $targetPath = null): int {
    global $root;
    // Only meaningful for Python sources; skip for other file types to avoid a costly fs walk.
    if ($targetPath !== null && strtolower(pathinfo($targetPath, PATHINFO_EXTENSION)) !== 'py') return 0;
    // Scope the sweep to the target's directory (or $root when no context), bounded by depth.
    $scope = $targetPath !== null ? dirname($targetPath) : $root;
    if (!is_dir($scope)) return 0;
    $maxDepth = $targetPath !== null ? 2 : 3;
    $command = 'count=$(find ' . escapeshellarg($scope) . ' -maxdepth ' . $maxDepth . ' -type d -name __pycache__ -print | wc -l); find ' . escapeshellarg($scope) . ' -maxdepth ' . $maxDepth . ' -type d -name __pycache__ -exec rm -rf -- {} + 2>/dev/null; printf "%s\\n" "$count"';
    $result = runCommand(['/bin/bash', '-lc', $command], $root, 30);
    if ($result['returncode'] !== 0) throw new RuntimeException('pycache cleanup failed: ' . $result['stderr']);
    return (int)trim($result['stdout']);
}

function syntaxCheck(string $path, string $content): array {
    $ext = strtolower(pathinfo($path, PATHINFO_EXTENSION));
    if (!in_array($ext, ['py', 'js', 'mjs', 'cjs', 'php'], true)) {
        return ['checked' => false, 'ok' => true];
    }
    $tmp = tempnam(sys_get_temp_dir(), 'mcp-syntax-');
    if ($tmp === false) throw new RuntimeException('cannot create syntax-check temporary file');
    $tmpPath = $tmp . '.' . $ext;
    @rename($tmp, $tmpPath);
    try {
        file_put_contents($tmpPath, $content);
        $cmd = match ($ext) {
            'py' => ['/bin/bash', '-lc', 'python3 -c ' . escapeshellarg('import ast,sys; ast.parse(sys.stdin.read())') . ' < ' . escapeshellarg($tmpPath) . ' 2>&1'],
            'php' => ['php', '-l', $tmpPath],
            default => ['node', '--check', $tmpPath]
        };
        $result = runCommand($cmd, sys_get_temp_dir(), 30);
        $ok = !$result['timed_out'] && $result['returncode'] === 0;
        $message = trim($result['stdout'] . $result['stderr']);
        return ['checked' => true, 'ok' => $ok, 'message' => $ok ? null : str_replace($tmpPath, '<content>', $message)];
    } finally {
        @unlink($tmpPath);
    }
}

function validateAndClean(string $path, string $content, bool $check = true): array {
    $syntax = $check ? syntaxCheck($path, $content) : ['checked' => false, 'ok' => true];
    if (!$syntax['ok']) {
        return ['syntax_ok' => false, 'error' => $syntax['message']];
    }
    return ['syntax_ok' => $syntax['checked'] ? true : null];
}

function lineOffsets(string $text): array {
    $offsets = [0];
    $length = strlen($text);
    for ($i = 0; $i < $length; $i++) {
        if ($text[$i] === "\r") {
            if ($i + 1 < $length && $text[$i + 1] === "\n") $i++;
            $offsets[] = $i + 1;
        } elseif ($text[$i] === "\n") {
            $offsets[] = $i + 1;
        }
    }
    return $offsets;
}

function listDirectory(array $a): array {
    $cwd = $a['cwd'] ?? '.';
    $target = resolvePath($a['path'] ?? '.', $cwd, true);
    if (!is_dir($target)) throw new RuntimeException("not a directory: $target");
    $hidden = !empty($a['include_hidden']);
    $recursive = !empty($a['recursive']);
    $find = 'find ' . escapeshellarg($target) . ($recursive ? '' : ' -mindepth 1 -maxdepth 1') . ' -mindepth 1' . ($hidden ? '' : " ! -name '.*'") . ' -printf ' . escapeshellarg("%p\\0%y\\0%s\\0%T@\\0") . ' 2>&1';
    $run = runCommand(['/bin/bash', '-lc', $find], $target);
    if ($run['returncode'] !== 0) throw new RuntimeException(trim($run['stderr'] . $run['stdout']));
    $parts = explode("\0", $run['stdout']);
    $entries = [];
    for ($i = 0; $i + 3 < count($parts) && count($entries) < MAX_SEARCH_RESULTS; $i += 4) {
        if ($parts[$i] === '') continue;
        $entries[] = ['path' => $parts[$i], 'type' => match ($parts[$i + 1]) {'d' => 'dir', 'l' => 'link', default => 'file'}, 'size' => (int)$parts[$i + 2], 'modified' => (float)$parts[$i + 3]];
    }
    return ['root' => $target, 'count' => count($entries), 'entries' => $entries];
}

function readFileTool(array $a): array {
    $target = resolvePath($a['path'], $a['cwd'] ?? '.', true);
    if (!is_file($target)) throw new RuntimeException("not a file: $target");
    $size = filesize($target);
    if ($size > MAX_READ_BYTES) throw new RuntimeException("file too large ($size bytes); max " . MAX_READ_BYTES);
    $text = file_get_contents($target);
    if ($text === false) throw new RuntimeException("cannot read: $target");
    $offsets = lineOffsets($text);
    $total = count($offsets);
    if ($size > 0 && end($offsets) === $size) $total--;
    $lo = max(1, (int)($a['start_line'] ?? 1));
    $hi = min((int)($a['end_line'] ?? $total), $total);
    $content = '';
    if ($hi >= $lo && isset($offsets[$lo - 1])) {
        $start = $offsets[$lo - 1];
        $end = $hi < $total ? $offsets[$hi] : strlen($text);
        $content = substr($text, $start, max(0, $end - $start));
    }
    return ['path' => $target, 'total_lines' => $total, 'returned_range' => [$lo, $hi], 'content' => $content];
}

function writeFileTool(array $a): array {
    $target = resolvePath($a['path'], $a['cwd'] ?? '.');
    $append = (bool)($a['append'] ?? false);
    $createDirs = (bool)($a['create_dirs'] ?? true);
    $check = (bool)($a['check_syntax'] ?? true);
    if ($createDirs && !is_dir(dirname($target))) mkdir(dirname($target), 0775, true);
    $old = is_file($target) ? file_get_contents($target) : '';
    $resulting = $append ? $old . $a['content'] : $a['content'];
    $validation = validateAndClean($target, $resulting, $check);
    if (($validation['syntax_ok'] ?? null) === false) return ['path' => $target, 'written' => false, 'syntax_ok' => false, 'error' => $validation['error'], 'message' => 'файл не змінено: помилка синтаксису (передайте check_syntax=False для примусового запису)'];
    if (file_put_contents($target, $a['content'], LOCK_EX | ($append ? FILE_APPEND : 0)) === false) throw new RuntimeException("write failed: $target");
    $validation['pycache_removed'] = cleanupPycache($target);
    return ['path' => $target, 'written' => true, 'bytes_written' => strlen($a['content']), 'mode' => $append ? 'append' : 'overwrite'] + array_filter($validation, fn($v) => $v !== null);
}

function updateFileTool(array $a): array {
    $target = resolvePath($a['path'], $a['cwd'] ?? '.', true);
    if (!is_file($target)) throw new RuntimeException("not a file: $target");
    $original = file_get_contents($target);
    $regex = (bool)($a['regex'] ?? false);
    $occurrence = $a['occurrence'] ?? null;
    $find = $a['find'];
    $replace = $a['replace'];
    if ($regex) {
        $pattern = '~' . str_replace('~', '\\~', $find) . '~';
        $matches = [];
        if (@preg_match_all($pattern, $original, $matches, PREG_OFFSET_CAPTURE) === false) throw new RuntimeException('invalid regex');
        $found = $matches[0] ?? [];
        $selected = $occurrence === null ? $found : (isset($found[max(0, (int)$occurrence - 1)]) ? [$found[(int)$occurrence - 1]] : []);
        $newText = $original;
        foreach (array_reverse($selected) as [$matched, $offset]) $newText = substr_replace($newText, $replace, $offset, strlen($matched));
        $count = count($selected);
    } else {
        $positions = [];
        $offset = 0;
        while (($pos = strpos($original, $find, $offset)) !== false) {
            $positions[] = $pos;
            $offset = $pos + max(1, strlen($find));
        }
        $selected = $occurrence === null ? $positions : (isset($positions[(int)$occurrence - 1]) ? [$positions[(int)$occurrence - 1]] : []);
        $newText = $original;
        foreach (array_reverse($selected) as $pos) $newText = substr_replace($newText, $replace, $pos, strlen($find));
        $count = count($selected);
    }
    if ($count === 0) {
        $diff = "No matching text found.\n";
        $diff .= shellDiff($original, $original . "\n[requested pattern not found: " . $find . "]\n", $target);
        return ['path' => $target, 'replacements' => 0, 'changed' => false, 'message' => 'no match found', 'diff' => $diff];
    }
    $validation = validateAndClean($target, $newText, (bool)($a['check_syntax'] ?? true));
    if (($validation['syntax_ok'] ?? null) === false) return ['path' => $target, 'replacements' => $count, 'changed' => false, 'syntax_ok' => false, 'error' => $validation['error'], 'message' => 'файл не змінено: правка ламає синтаксис (передайте check_syntax=False для примусового збереження)'];
    file_put_contents($target, $newText, LOCK_EX);
    $validation['pycache_removed'] = cleanupPycache($target);
    return ['path' => $target, 'replacements' => $count, 'changed' => true] + array_filter($validation, fn($v) => $v !== null);
}

function shellDiff(string $old, string $new, string $label): string {
    $a = tempnam(sys_get_temp_dir(), 'mcp-diff-a-');
    $b = tempnam(sys_get_temp_dir(), 'mcp-diff-b-');
    file_put_contents($a, $old);
    file_put_contents($b, $new);
    try {
        $r = runCommand(['diff', '-u', '--label', $label, '--label', $label . ' (requested)', $a, $b], sys_get_temp_dir(), 30);
        return $r['stdout'] ?: $r['stderr'];
    } finally {
        @unlink($a);
        @unlink($b);
    }
}

function deletePathTool(array $a): array {
    $target = resolvePath($a['path'], $a['cwd'] ?? '.', true);
    if (is_dir($target) && !is_link($target)) {
        if (!($a['recursive'] ?? false)) {
            if (count(scandir($target) ?: []) > 2) throw new RuntimeException("каталог не порожній: $target (передайте recursive=True для рекурсивного видалення)");
            rmdir($target);
        } else {
            $r = runCommand(['/bin/bash', '-lc', 'rm -rf -- ' . escapeshellarg($target)], dirname($target));
            if ($r['returncode'] !== 0) throw new RuntimeException($r['stderr']);
        }
        return ['path' => $target, 'type' => 'dir', 'deleted' => true, 'recursive' => (bool)($a['recursive'] ?? false)];
    }
    if (!unlink($target)) throw new RuntimeException("delete failed: $target");
    return ['path' => $target, 'type' => 'file', 'deleted' => true, 'recursive' => false];
}

function renamePathTool(array $a): array {
    $src = resolvePath($a['src'], $a['cwd'] ?? '.', true);
    $dst = resolvePath($a['dst'], $a['cwd'] ?? '.');
    if ($src === $dst) throw new RuntimeException('src and dst resolve to the same path');
    if (file_exists($dst) && !($a['overwrite'] ?? false)) throw new RuntimeException("destination exists: $dst (передайте overwrite=True для заміни)");
    if (is_dir($src) && str_starts_with($dst . '/', $src . '/')) throw new RuntimeException("не можна перемістити каталог усередину самого себе: $src -> $dst");
    if (($a['create_dirs'] ?? true) && !is_dir(dirname($dst))) mkdir(dirname($dst), 0775, true);
    $r = runCommand(['/bin/bash', '-lc', 'mv ' . (($a['overwrite'] ?? false) ? '-f' : '-n') . ' -- ' . escapeshellarg($src) . ' ' . escapeshellarg($dst)], dirname($src));
    if ($r['returncode'] !== 0) throw new RuntimeException("rename failed: " . $r['stderr']);
    return ['from' => $src, 'to' => $dst, 'renamed' => true, 'overwrite' => (bool)($a['overwrite'] ?? false)];
}

function searchFilesTool(array $a): array {
    $base = resolvePath($a['root'] ?? '.', $a['cwd'] ?? '.', true);
    $pattern = $a['pattern'];
    $glob = $a['glob'] ?? '*';
    $case = (bool)($a['case_sensitive'] ?? false);
    $limit = min(MAX_SEARCH_RESULTS, max(1, (int)($a['max_results'] ?? MAX_SEARCH_RESULTS)));
    $cmd = 'find ' . escapeshellarg($base) . ' -type f -name ' . escapeshellarg($glob) . ' -printf ' . escapeshellarg('%p\n');
    $r = runCommand(['/bin/bash', '-lc', $cmd], $base);
    $names = [];
    foreach (preg_split('/\r?\n/', trim($r['stdout'])) as $file) {
        if ($file === '') continue;
        $hay = basename($file);
        if (($case ? str_contains($hay, $pattern) : stripos($hay, $pattern) !== false)) $names[] = $file;
        if (count($names) >= $limit) break;
    }
    $contents = [];
    if ($a['search_contents'] ?? false) {
        $rg = ['rg', '--no-heading', '--line-number', '--fixed-strings', '--max-count', (string)$limit, '--glob', $glob, '--', $pattern, $base];
        if (!$case) array_splice($rg, 1, 0, ['--ignore-case']);
        $r = runCommand($rg, $base);
        foreach (preg_split('/\r?\n/', trim($r['stdout'])) as $line) {
            if ($line === '') continue;
            if (preg_match('/^(.*?):(\d+):(.*)$/', $line, $m)) $contents[] = ['path' => $m[1], 'line' => (int)$m[2], 'text' => substr(trim($m[3]), 0, 300)];
            if (count($contents) >= $limit) break;
        }
    }
    return ['root' => $base, 'pattern' => $pattern, 'name_matches' => $names, 'content_matches' => $contents];
}

function executeCommandTool(array $a): array {
    $cwd = basePath($a['cwd'] ?? '.');
    $command = (string)($a['command'] ?? '');
    if (trim($command) === '') throw new RuntimeException('empty command');
    $shell = (bool)($a['shell'] ?? false);
    // Auto-enable shell mode when the command contains shell metacharacters or multiple statements.
    // This makes ChatGPT-style clients work even if they forget to set shell=true.
    if (!$shell && preg_match('/[;&|<>$\x60\n\\`]|\$\{|\*|\?/', $command)) {
        $shell = true;
    }
    $argv = $shell ? ['/bin/sh', '-c', $command] : preg_split('/\s+/', trim($command));
    if (!$shell && (!$argv || $argv[0] === '')) throw new RuntimeException('empty command');
    $r = runCommand($argv, $cwd, min(max(0.1, (float)($a['timeout'] ?? 60)), EXEC_TIMEOUT_MAX));
    return ['command' => $command, 'shell' => $shell, 'returncode' => $r['returncode'], 'stdout' => substr($r['stdout'], -MAX_READ_BYTES), 'stderr' => substr($r['stderr'], -MAX_READ_BYTES), 'timed_out' => $r['timed_out']];
}

$tools = [
    ['name' => 'list_directory', 'description' => 'Перелічує записи каталогу.\n\nArgs:\n    path: Каталог для перегляду.\n    recursive: Обходити все дерево, якщо True.\n    include_hidden: Включати приховані файли (з крапкою), якщо True.\n    cwd: Робочий каталог; відносні шляхи розв\'язуються від нього (типово — домашній каталог MCP-користувача).', 'inputSchema' => ['type' => 'object', 'properties' => ['path' => ['type' => 'string', 'default' => '.'], 'recursive' => ['type' => 'boolean', 'default' => false], 'include_hidden' => ['type' => 'boolean', 'default' => false], 'cwd' => ['type' => 'string', 'default' => '.']]]],
    ['name' => 'search_files', 'description' => 'Шукає у файловій системі за іменем файлу, за бажанням — за вмістом (через ripgrep).\n\nArgs:\n    pattern: Підрядок для пошуку в імені та у вмісті (обробляється як літеральний рядок, не як regex).\n    root: Каталог, всередині якого шукати.\n    search_contents: Якщо True, також шукати pattern усередині текстових файлів.\n    glob: Брати до уваги лише файли, чиє ім\'я відповідає цьому glob (напр. "*.py").\n    case_sensitive: Враховувати регістр, якщо True.\n    max_results: Обмеження на кількість повернутих збігів.\n    cwd: Робочий каталог; відносний root розв\'язується від нього.', 'inputSchema' => ['type' => 'object', 'properties' => ['pattern' => ['type' => 'string'], 'root' => ['type' => 'string', 'default' => '.'], 'search_contents' => ['type' => 'boolean', 'default' => false], 'glob' => ['type' => 'string', 'default' => '*'], 'case_sensitive' => ['type' => 'boolean', 'default' => false], 'max_results' => ['type' => 'integer', 'default' => 100], 'cwd' => ['type' => 'string', 'default' => '.']], 'required' => ['pattern']]],
    ['name' => 'read_file', 'description' => 'Читає текстовий файл, за бажанням — конкретний діапазон рядків (відлік з 1).\n\nArgs:\n    path: Файл для читання.\n    start_line: Перший рядок для повернення (відлік з 1, включно).\n    end_line: Останній рядок для повернення (відлік з 1, включно); None означає до кінця файлу.\n    cwd: Робочий каталог; відносні шляхи розв\'язуються від нього (типово — домашній каталог MCP-користувача).', 'inputSchema' => ['type' => 'object', 'properties' => ['path' => ['type' => 'string'], 'start_line' => ['type' => 'integer', 'default' => 1], 'end_line' => ['type' => ['integer', 'null'], 'default' => null], 'cwd' => ['type' => 'string', 'default' => '.']], 'required' => ['path']]],
    ['name' => 'write_file', 'description' => 'Створює або перезаписує (або дозаписує) текстовий файл.\n\nДля файлів .py/.js/.php підсумковий вміст перевіряється на синтаксис до запису; якщо перевірка не пройшла, файл лишається недоторканим (якщо check_syntax не False).\n\nЩойно створені файли та каталоги успадковують власника/групу найближчого існуючого батьківського каталогу (best-effort; пропускається, коли не дозволено).\n\nArgs:\n    path: Файл призначення.\n    content: Текст для запису.\n    append: Дозаписувати замість обрізання, коли True.\n    create_dirs: Створювати батьківські каталоги, якщо вони відсутні.\n    check_syntax: Валідувати синтаксис для .py/.js/.php перед записом (типово True).\n    cwd: Робочий каталог; відносні шляхи розв\'язуються від нього.', 'inputSchema' => ['type' => 'object', 'properties' => ['path' => ['type' => 'string'], 'content' => ['type' => 'string'], 'append' => ['type' => 'boolean', 'default' => false], 'create_dirs' => ['type' => 'boolean', 'default' => true], 'check_syntax' => ['type' => 'boolean', 'default' => true], 'cwd' => ['type' => 'string', 'default' => '.']], 'required' => ['path', 'content']]],
    ['name' => 'update_file', 'description' => 'Виконує цільову заміну (find-and-replace) всередині наявного файлу.\n\nДля файлів .py/.js/.php відредагований вміст перевіряється на синтаксис до запису; якщо перевірка не пройшла, файл лишається недоторканим (якщо check_syntax не False).\n\nArgs:\n    path: Файл для редагування.\n    find: Текст (або regex, коли regex).\n    replace: Текст заміни.\n    occurrence: Індекс (відлік з 1), котрий збіг замінити; None замінює всі.\n    regex: Вважати find регулярним виразом, коли True.\n    check_syntax: Валідувати синтаксис для .py/.js/.php перед збереженням (типово True).\n    cwd: Робочий каталог; відносні шляхи розв\'язуються від нього.', 'inputSchema' => ['type' => 'object', 'properties' => ['path' => ['type' => 'string'], 'find' => ['type' => 'string'], 'replace' => ['type' => 'string'], 'occurrence' => ['type' => ['integer', 'null'], 'default' => null], 'regex' => ['type' => 'boolean', 'default' => false], 'check_syntax' => ['type' => 'boolean', 'default' => true], 'cwd' => ['type' => 'string', 'default' => '.']], 'required' => ['path', 'find', 'replace']]],
    ['name' => 'execute_command', 'description' => 'Виконує команду оболонки та захоплює її вивід.\n\nArgs:\n    command: Командний рядок для запуску. Коли shell False — розбирається через shlex; коли True — передається системній оболонці.\n    cwd: Робочий каталог для процесу. Відносні шляхи та пусте значення розв\'язуються від домашнього каталогу MCP-користувача.\n    timeout: Максимум секунд реального часу, після яких процес буде завершено примусово.\n    shell: Виконати через /bin/sh -c замість exec-форми.', 'inputSchema' => ['type' => 'object', 'properties' => ['command' => ['type' => 'string'], 'cwd' => ['type' => 'string', 'default' => '.'], 'timeout' => ['type' => 'number', 'default' => 60], 'shell' => ['type' => 'boolean', 'default' => false]], 'required' => ['command']]],
    ['name' => 'delete_path', 'description' => 'Видаляє файл, символічне посилання або каталог.\n\nArgs:\n    path: Файл чи каталог для видалення.\n    recursive: Видаляти непорожнений каталог з деревом, якщо True. Каталог видаляється, лише якщо він порожній (або recursive True).\n    cwd: Робочий каталог; відносні шляхи розв\'язуються від нього.', 'inputSchema' => ['type' => 'object', 'properties' => ['path' => ['type' => 'string'], 'recursive' => ['type' => 'boolean', 'default' => false], 'cwd' => ['type' => 'string', 'default' => '.']], 'required' => ['path']]],
    ['name' => 'rename_path', 'description' => 'Перейменовує або переміщує файл чи каталог.\n\nПрацює і для файлів, і для каталогів. Обидва шляхи src і dst розв\'язуються та обмежуються коренем MCP_FS_ROOT.\n\nArgs:\n    src: Наявний файл або каталог для перейменування/переміщення.\n    dst: Новий шлях/назва (може бути в іншому каталозі).\n    overwrite: Замінити dst, якщо він уже існує, коли True; інакше операцію відмовлено.\n    create_dirs: Створити батьківський каталог dst, якщо він відсутній.\n    cwd: Робочий каталог; відносні шляхи src/dst розв\'язуються від нього.', 'inputSchema' => ['type' => 'object', 'properties' => ['src' => ['type' => 'string'], 'dst' => ['type' => 'string'], 'overwrite' => ['type' => 'boolean', 'default' => false], 'create_dirs' => ['type' => 'boolean', 'default' => true], 'cwd' => ['type' => 'string', 'default' => '.']], 'required' => ['src', 'dst']]]
];

foreach ($tools as &$tool) { $tool['description'] = str_replace('\\n', "\n", $tool['description']); }
unset($tool);

$handlers = ['list_directory' => 'listDirectory', 'search_files' => 'searchFilesTool', 'read_file' => 'readFileTool', 'write_file' => 'writeFileTool', 'update_file' => 'updateFileTool', 'execute_command' => 'executeCommandTool', 'delete_path' => 'deletePathTool', 'rename_path' => 'renamePathTool'];

function handleParsed(mixed $request): ?array {
    $id = null;
    try {
        if (!is_array($request)) {
            return ['jsonrpc' => '2.0', 'id' => null, 'error' => ['code' => -32600, 'message' => 'Invalid Request']];
        }
        $id = $request['id'] ?? null;
        $method = $request['method'] ?? '';
        if ($method === 'notifications/initialized' || $id === null) return null;
        $response = ['jsonrpc' => '2.0', 'id' => $id];
        switch ($method) {
            case 'initialize':
                $response['result'] = ['protocolVersion' => MCP_PROTOCOL, 'capabilities' => ['tools' => ['listChanged' => false]], 'serverInfo' => ['name' => 'mcp-filesystem', 'version' => '2.0.0']];
                break;
            case 'ping':
                $response['result'] = new stdClass();
                break;
            case 'tools/list':
                global $tools;
                $response['result'] = ['tools' => $tools];
                break;
            case 'tools/call':
                global $handlers;
                $name = $request['params']['name'] ?? '';
                $args = $request['params']['arguments'] ?? [];
                $user = auditUser();
                $started = microtime(true);
                auditLog('info', 'tool_call start user=' . $user . ' name=' . $name . ' args=' . json_encode(auditCompact($args), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE));
                try {
                    if (!isset($handlers[$name])) throw new RuntimeException("Unknown tool: $name");
                    $toolResponse = toolResult($handlers[$name]($args));
                    $duration = (microtime(true) - $started) * 1000;
                    if (auditIsError($toolResponse)) {
                        auditLog('warning', 'tool_call done user=' . $user . ' name=' . $name . ' duration_ms=' . number_format($duration, 1, '.', '') . ' is_error=True error=' . json_encode(auditErrorText($toolResponse), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
                    } else {
                        auditLog('info', 'tool_call done user=' . $user . ' name=' . $name . ' duration_ms=' . number_format($duration, 1, '.', '') . ' is_error=False');
                    }
                    $response['result'] = $toolResponse;
                } catch (Throwable $e) {
                    $duration = (microtime(true) - $started) * 1000;
                    auditLog('error', 'tool_call error user=' . $user . ' name=' . $name . ' duration_ms=' . number_format($duration, 1, '.', '') . ' exc=' . $e->getMessage());
                    throw $e;
                }
                break;
            default:
                $response['error'] = ['code' => -32601, 'message' => "Method not found: $method"];
        }
        return $response;
    } catch (Throwable $e) {
        if ($id === null) return null;
        return ['jsonrpc' => '2.0', 'id' => $id, 'result' => toolResult($e->getMessage(), true)];
    }
}

if ($__IS_CLI) {
    // --- stdio transport (newline-delimited JSON-RPC) ---
    while (($line = fgets(STDIN)) !== false) {
        $line = trim($line);
        if ($line === '') continue;
        try {
            $decoded = json_decode($line, true, 512, JSON_THROW_ON_ERROR);
        } catch (Throwable $e) {
            emit(['jsonrpc' => '2.0', 'id' => null, 'error' => ['code' => -32700, 'message' => 'Parse error: ' . $e->getMessage()]]);
            continue;
        }
        $resp = handleParsed($decoded);
        if ($resp !== null) emit($resp);
    }
    exit(0);
}

// --- Streamable HTTP transport (ChatGPT / Claude web MCP clients) ---
$httpMethod = $_SERVER['REQUEST_METHOD'] ?? 'GET';

if ($httpMethod === 'OPTIONS') {
    http_response_code(204);
    exit;
}

if ($httpMethod === 'GET') {
    // Server-side SSE stream not implemented; tell clients to use POST.
    header('Allow: POST, OPTIONS');
    http_response_code(405);
    header('Content-Type: application/json');
    echo json_encode(['jsonrpc' => '2.0', 'id' => null, 'error' => ['code' => -32600, 'message' => 'GET (SSE stream) not supported; use POST']]);
    exit;
}

if ($httpMethod === 'DELETE') {
    // Session termination — nothing to terminate in stateless mode.
    http_response_code(200);
    exit;
}

if ($httpMethod !== 'POST') {
    header('Allow: POST, OPTIONS');
    http_response_code(405);
    header('Content-Type: application/json');
    echo json_encode(['jsonrpc' => '2.0', 'id' => null, 'error' => ['code' => -32600, 'message' => 'Only POST is supported']]);
    exit;
}

$raw = file_get_contents('php://input');
if ($raw === false || trim($raw) === '') {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['jsonrpc' => '2.0', 'id' => null, 'error' => ['code' => -32700, 'message' => 'Empty request body']]);
    exit;
}

try {
    $payload = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
} catch (Throwable $e) {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['jsonrpc' => '2.0', 'id' => null, 'error' => ['code' => -32700, 'message' => 'Parse error: ' . $e->getMessage()]]);
    exit;
}

$isBatch = is_array($payload) && array_is_list($payload) && $payload !== [];
$messages = $isBatch ? $payload : [$payload];

$responses = [];
$hasInitialize = false;
foreach ($messages as $m) {
    if (is_array($m) && ($m['method'] ?? '') === 'initialize' && isset($m['id'])) $hasInitialize = true;
    $resp = handleParsed($m);
    if ($resp !== null) $responses[] = $resp;
}

header('Content-Type: application/json');

if ($hasInitialize) {
    $sessionId = bin2hex(random_bytes(16));
    header('Mcp-Session-Id: ' . $sessionId);
    header('Access-Control-Expose-Headers: Mcp-Session-Id');
}

if ($responses === []) {
    // Only notifications arrived — accept with 202 and empty body.
    http_response_code(202);
    exit;
}

http_response_code(200);
echo json_encode($isBatch ? $responses : $responses[0], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
exit;
