<?php
define("IPT_UID", "2202978");
define("IPT_PASS", "wna9SiPrdLIUXaXtWreffc8U7LT1GPx5");
define("IPT_COOKIE", "uid=" . IPT_UID . "; pass=" . IPT_PASS);
define("TD_UID", "2956419");
define("TD_PASS", "PasF9zdgqQbTn6XrlZ2bHnc62vDAcUfM");
define("TD_COOKIE", "uid=" . TD_UID . "; pass=" . TD_PASS);
define("QBIT_API", "http://127.0.0.1:18080/api/v2");

// Login to qBit
$ch = curl_init(QBIT_API . "/auth/login");
curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER=>true, CURLOPT_POST=>true,
    CURLOPT_POSTFIELDS=>"username=admin&password=adminadmin",
    CURLOPT_COOKIEJAR=>"/tmp/qbit.cookie", CURLOPT_COOKIEFILE=>"/tmp/qbit.cookie"]);
curl_exec($ch); curl_close($ch);

function searchTracker($query, $cookie, $cats) {
    $clean = preg_replace('/[^a-zA-Z0-9\s]/', '', $query);
    $clean = preg_replace('/\s+/', '+', trim($clean));
    $host = (strpos($cookie, '2202978') !== false) ? 'iptorrents.com' : 'torrentday.com';
    $url = "https://$host/t.rss?download;q=$clean;$cats";
    $ch = curl_init($url);
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_COOKIE => $cookie,
        CURLOPT_ENCODING => '', CURLOPT_TIMEOUT => 15, CURLOPT_SSL_VERIFYPEER => false, CURLOPT_FOLLOWLOCATION => true]);
    $rss = curl_exec($ch); curl_close($ch);
    if (empty($rss)) return [];
    preg_match_all('/<item>\s*<title>(.*?)<\/title>\s*<link>(.*?)<\/link>.*?<description>(.*?)<\/description>/s', $rss, $matches);
    $results = [];
    foreach (($matches[1] ?? []) as $i => $title) {
        $title = html_entity_decode($title, ENT_QUOTES);
        if (stripos($title, '1080p') === false) continue;
        if (preg_match('/cam|telesync|hdts|hindi|tamil|telugu|korean/i', $title)) continue;
        $desc = $matches[3][$i] ?? '';
        $seeders = 0; $sizeGB = 99;
        if (preg_match('/S:(\d+)/i', $desc, $sm)) $seeders = (int)$sm[1];
        if (preg_match('/([\d.]+)\s*GB/i', $desc, $szm)) $sizeGB = (float)$szm[1];
        if (preg_match('/([\d.]+)\s*MB/i', $desc, $szm)) $sizeGB = (float)$szm[1] / 1024;
        $score = ($seeders * 2) - $sizeGB;
        $results[] = ['title' => $title, 'url' => html_entity_decode($matches[2][$i], ENT_QUOTES),
            'seeders' => $seeders, 'sizeGB' => round($sizeGB, 2), 'score' => round($score, 1),
            'source' => strtoupper(substr($host, 0, 3))];
    }
    usort($results, fn($a,$b) => $b['score'] <=> $a['score']);
    return $results;
}

function downloadAndAdd($best) {
    $dlUrl = preg_replace('/\?torrent_pass=.*$/', '', $best['url']);
    $dlUrl = str_replace(' ', '%20', $dlUrl);
    $cookie = ($best['source'] === 'IPT') ? IPT_COOKIE : TD_COOKIE;
    $ch = curl_init($dlUrl);
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_COOKIE => $cookie, CURLOPT_TIMEOUT => 30, CURLOPT_SSL_VERIFYPEER => false]);
    $torrentData = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($httpCode !== 200 || empty($torrentData) || $torrentData[0] !== 'd') {
        return "Download failed: HTTP $httpCode";
    }
    $tmpFile = '/tmp/euphoria_' . md5($dlUrl) . '.torrent';
    file_put_contents($tmpFile, $torrentData);
    $ch = curl_init(QBIT_API . '/torrents/add');
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => ['torrents' => new CURLFile($tmpFile, 'application/x-bittorrent'), 'savepath' => '/home/user/Downloads/'],
        CURLOPT_COOKIEFILE => "/tmp/qbit.cookie"]);
    $result = curl_exec($ch); curl_close($ch);
    @unlink($tmpFile);
    return trim($result) === 'Ok.' ? 'OK' : "qBit: $result";
}

// Episodes to search: S01 (8 eps), S02 (8 eps), Specials (2), S03 (1 aired so far)
$episodes = [];

// Season 0 specials
$episodes[] = ['s' => 0, 'e' => 1, 'search' => 'Euphoria Special Part 1 1080p'];
$episodes[] = ['s' => 0, 'e' => 2, 'search' => 'Euphoria Special Part 2 1080p'];

// Season 1: 8 episodes
for ($e = 1; $e <= 8; $e++) {
    $episodes[] = ['s' => 1, 'e' => $e, 'search' => sprintf('Euphoria S01E%02d 1080p', $e)];
}

// Season 2: 8 episodes
for ($e = 1; $e <= 8; $e++) {
    $episodes[] = ['s' => 2, 'e' => $e, 'search' => sprintf('Euphoria S02E%02d 1080p', $e)];
}

// Season 3: episode 1 aired April 12
$episodes[] = ['s' => 3, 'e' => 1, 'search' => 'Euphoria S03E01 1080p'];

// Also try season packs first
$packs = [
    ['s' => 1, 'search' => 'Euphoria S01 Complete 1080p'],
    ['s' => 1, 'search' => 'Euphoria Season 1 1080p'],
    ['s' => 2, 'search' => 'Euphoria S02 Complete 1080p'],
    ['s' => 2, 'search' => 'Euphoria Season 2 1080p'],
];

echo "=== Searching for Season Packs first ===\n\n";
$foundPacks = [];
foreach ($packs as $pack) {
    $results = array_merge(
        searchTracker($pack['search'], IPT_COOKIE, '5;48;44;3;11;100;101'),
        searchTracker($pack['search'], TD_COOKIE, '5;48;44;3;11')
    );
    // Filter to actual packs (not single episodes)
    $packResults = array_filter($results, fn($r) => !preg_match('/S\d{2}E\d{2}/i', $r['title']));
    if (!empty($packResults)) {
        $packResults = array_values($packResults);
        usort($packResults, fn($a,$b) => $b['score'] <=> $a['score']);
        $best = $packResults[0];
        echo "PACK S{$pack['s']}: [{$best['source']}] {$best['seeders']}s | {$best['sizeGB']}GB | {$best['title']}\n";
        $result = downloadAndAdd($best);
        echo "  → $result\n\n";
        if ($result === 'OK') $foundPacks[] = $pack['s'];
    }
}

echo "\n=== Searching for Individual Episodes ===\n\n";
$downloaded = 0;
foreach ($episodes as $ep) {
    // Skip if we got the pack for this season
    if (in_array($ep['s'], $foundPacks)) {
        echo "S{$ep['s']}E{$ep['e']}: Skipped (have season pack)\n";
        continue;
    }

    $results = array_merge(
        searchTracker($ep['search'], IPT_COOKIE, '5;48;44;3;11;100;101'),
        searchTracker($ep['search'], TD_COOKIE, '5;48;44;3;11')
    );

    // Also try with "US" prefix
    if (empty($results)) {
        $altSearch = str_replace('Euphoria', 'Euphoria US', $ep['search']);
        $results = array_merge(
            searchTracker($altSearch, IPT_COOKIE, '5;48;44;3;11;100;101'),
            searchTracker($altSearch, TD_COOKIE, '5;48;44;3;11')
        );
    }

    // Also try without "1080p" if nothing found
    if (empty($results)) {
        $altSearch = str_replace(' 1080p', '', $ep['search']);
        $altResults = array_merge(
            searchTracker($altSearch, IPT_COOKIE, '5;48;44;3;11;100;101'),
            searchTracker($altSearch, TD_COOKIE, '5;48;44;3;11')
        );
        // Still filter for 1080p in results (already done in searchTracker)
        $results = $altResults;
    }

    if (empty($results)) {
        echo "S{$ep['s']}E{$ep['e']}: NOT FOUND\n";
        continue;
    }

    $best = $results[0];
    echo "S{$ep['s']}E{$ep['e']}: [{$best['source']}] {$best['seeders']}s | {$best['sizeGB']}GB | {$best['title']}\n";
    $result = downloadAndAdd($best);
    echo "  → $result\n";
    $downloaded++;
    usleep(500000); // 0.5s delay
}

echo "\n=== Done: $downloaded episodes downloaded ===\n";
