<?php

declare(strict_types=1);

// Standalone packaging regressions; no PHPUnit or real documents needed.
$root = sys_get_temp_dir() . '/ainonymify-assets-test-' . bin2hex(random_bytes(6));
function check(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}
function removeTree(string $path): void
{
    if (is_dir($path)) {
        foreach (new FilesystemIterator($path) as $item) {
            removeTree($item->getPathname());
        }
        rmdir($path);
    } elseif (file_exists($path)) {
        unlink($path);
    }
}
function runPublisher(string $root): int
{
    exec(escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg($root . '/scripts/install_frontend_assets.php') . ' 2>&1', $output, $status);
    return $status;
}
try {
    mkdir($root . '/scripts', 0755, true);
    copy(dirname(__DIR__) . '/install_frontend_assets.php', $root . '/scripts/install_frontend_assets.php');
    file_put_contents($root . '/composer.json', json_encode(['require' => ['npm-asset/pdfjs-dist' => '5.4.624']]));
    $package = $root . '/vendor/npm-asset/pdfjs-dist';
    foreach (['build', 'cmaps', 'standard_fonts', 'wasm'] as $dir) {
        mkdir($package . '/' . $dir, 0755, true);
    }
    file_put_contents($package . '/package.json', json_encode(['version' => '5.4.624']));
    foreach (['build/pdf.mjs', 'build/pdf.worker.mjs', 'LICENSE', 'cmaps/example.bcmap',
              'standard_fonts/LICENSE', 'standard_fonts/example.ttf', 'wasm/example.wasm'] as $file) {
        file_put_contents($package . '/' . $file, $file);
    }
    file_put_contents($package . '/not-for-publication.txt', 'not a browser asset');
    check(runPublisher($root) === 0, 'Initial publication failed');
    $public = $root . '/frontend/vendor/pdfjs';
    check(file_get_contents($public . '/pdf.mjs') === 'build/pdf.mjs', 'Incorrect PDF.js layout');
    check(is_file($public . '/pdf.worker.mjs') && is_file($public . '/LICENSE'), 'Missing worker or license');
    check(is_file($public . '/standard_fonts/LICENSE') && is_file($public . '/cmaps/example.bcmap')
          && is_file($public . '/wasm/example.wasm'), 'Missing supporting resources');
    check(!file_exists($public . '/not-for-publication.txt') && !file_exists($public . '/package.json'), 'Published private package files');
    file_put_contents($public . '/obsolete.txt', 'old version');
    check(runPublisher($root) === 0 && !file_exists($public . '/obsolete.txt'), 'Reinstall left stale assets');
    unlink($package . '/build/pdf.worker.mjs');
    check(runPublisher($root) !== 0, 'Incomplete package was accepted');
    check(is_file($public . '/pdf.worker.mjs'), 'Failed publication damaged previous assets');
    file_put_contents($package . '/package.json', json_encode(['version' => '0.0.0']));
    check(runPublisher($root) !== 0, 'Wrong package version was accepted');
    check(is_file($public . '/LICENSE'), 'Version mismatch damaged previous assets');
    fwrite(STDOUT, "PASS: publication layout, supporting licenses, idempotence and preservation on failure\n");
} finally {
    removeTree($root);
}
