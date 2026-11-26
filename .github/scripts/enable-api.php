#!/usr/bin/env php
<?php
/* Enables the REST API module of the CI instance and sets the API key of
 * user 1, the way the user card does (User::update() stores the key the way
 * the API access check expects). Reads DOLIBARR_HTDOCS and DOLIBARR_API_KEY.
 * Adapted from mdeweerd/dolibarr-agents (.ci/scripts/activate-agentportal.php).
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the Free
 * Software Foundation; either version 3 of the License, or (at your option)
 * any later version.
 */

if (PHP_SAPI !== 'cli') {
	echo "Error: this script must be run from the command line.\n";
	exit(1);
}

global $conf, $db, $langs, $user, $mysoc;

$htdocs = getenv('DOLIBARR_HTDOCS');
if (!$htdocs || !file_exists($htdocs . '/master.inc.php')) {
	fwrite(STDERR, 'DOLIBARR_HTDOCS does not point at an htdocs directory (got "' . $htdocs . '")' . "\n");
	exit(2);
}
$apiKey = getenv('DOLIBARR_API_KEY');
if (!$apiKey) {
	fwrite(STDERR, "DOLIBARR_API_KEY must be set.\n");
	exit(2);
}

require_once $htdocs . '/master.inc.php';
require_once DOL_DOCUMENT_ROOT . '/core/lib/admin.lib.php';
require_once DOL_DOCUMENT_ROOT . '/user/class/user.class.php';

$user = new User($db);
$user->fetch(1);

// The REST API is a module (descriptor modApi): on unless activated.
$result = activateModule('modApi');
if (!empty($result['errors'])) {
	fwrite(STDERR, 'API module activation failed: ' . implode(' | ', $result['errors']) . "\n");
	exit(1);
}
echo 'Module api activated (' . (int) $result['nbmodules'] . " module(s))\n";

$user->api_key = $apiKey;
if ($user->update($user) < 0) {
	fwrite(STDERR, 'Cannot set the api key of user 1: ' . $user->error . "\n");
	exit(1);
}
echo 'API key set for user ' . (int) $user->id . ' (' . $user->login . ")\n";
