-- A copy of the database must not use production's Bolagsverket credentials.
DELETE FROM ir_config_parameter WHERE key IN ('l10n_se_bolagsverket.client_secret', 'l10n_se_bolagsverket.enabled');
