<?php

declare(strict_types=1);

// Publish only browser assets. Composer metadata and the full npm package stay
// outside the web root. No network requests are made by this script.
$root = dirname(__DIR__);
$source = $root . '/vendor/npm-asset/pdfjs-dist';
$target = $root . '/frontend/vendor/pdfjs';
$manifest = json_decode(file_get_contents($root . '/composer.json'), true, flags: JSON_THROW_ON_ERROR);
$package = json_decode(file_get_contents($source . '/package.json'), true, flags: JSON_THROW_ON_ERROR);
if ($package['version'] !== $manifest['require']['npm-asset/pdfjs-dist']) {
    throw new RuntimeException('Installed PDF.js version does not match composer.json');
}
$files = ['build/pdf.mjs' => 'pdf.mjs', 'build/pdf.worker.mjs' => 'pdf.worker.mjs', 'LICENSE' => 'LICENSE',
          'cmaps' => 'cmaps', 'standard_fonts' => 'standard_fonts', 'wasm' => 'wasm'];
foreach (array_keys($files) as $file) {
    if (!file_exists($source . '/' . $file)) {
        throw new RuntimeException('Required PDF.js asset is missing: ' . $file);
    }
}

function copyAssets(string $source, string $target): void
{
    if (is_link($source)) {
        throw new RuntimeException('Unexpected symlink in PDF.js assets');
    }
    if (is_dir($source)) {
        if (!mkdir($target, 0755, true) && !is_dir($target)) {
            throw new RuntimeException('Cannot create asset directory');
        }
        foreach (new FilesystemIterator($source) as $item) {
            copyAssets($item->getPathname(), $target . '/' . $item->getFilename());
        }
    } elseif (!copy($source, $target)) {
        throw new RuntimeException('Cannot copy PDF.js asset');
    }
}

function removeAssets(string $path): void
{
    if (is_dir($path) && !is_link($path)) {
        foreach (new FilesystemIterator($path) as $item) {
            removeAssets($item->getPathname());
        }
        rmdir($path);
    } elseif (file_exists($path) || is_link($path)) {
        unlink($path);
    }
}

$parent = dirname($target);
if (!is_dir($parent) && !mkdir($parent, 0755, true)) {
    throw new RuntimeException('Cannot create frontend vendor directory');
}
$suffix = bin2hex(random_bytes(6));
$staged = $target . '.tmp-' . $suffix;
$backup = $target . '.old-' . $suffix;
mkdir($staged, 0755);
try {
    foreach ($files as $from => $to) {
        copyAssets($source . '/' . $from, $staged . '/' . $to);
    }
    if (file_exists($target) && !rename($target, $backup)) {
        throw new RuntimeException('Cannot replace previous PDF.js assets');
    }
    if (!rename($staged, $target)) {
        if (file_exists($backup)) {
            rename($backup, $target);
        }
        throw new RuntimeException('Cannot publish PDF.js assets');
    }
} finally {
    removeAssets($staged);
}
removeAssets($backup);
fwrite(STDOUT, 'Published local PDF.js ' . $package['version'] . " assets.\n");
