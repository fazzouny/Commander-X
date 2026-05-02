from __future__ import annotations

import unittest

import commander


class CommanderGateTests(unittest.TestCase):
    def test_validate_generated_command_allows_known_commands(self) -> None:
        self.assertEqual(commander.validate_generated_command("/status"), "/status")
        self.assertEqual(commander.validate_generated_command("/doctor"), "/doctor")
        self.assertEqual(commander.validate_generated_command("/inbox"), "/inbox")
        self.assertEqual(commander.validate_generated_command("/approvals"), "/approvals")
        self.assertEqual(commander.validate_generated_command("/audit"), "/audit")
        self.assertEqual(commander.validate_generated_command("/report"), "/report")
        self.assertEqual(commander.validate_generated_command("/report save"), "/report save")
        self.assertEqual(commander.validate_generated_command("/mission"), "/mission")
        self.assertEqual(commander.validate_generated_command("/mission example-app"), "/mission example-app")
        self.assertEqual(commander.validate_generated_command("/evidence"), "/evidence")
        self.assertEqual(commander.validate_generated_command("/evidence example-app"), "/evidence example-app")
        self.assertEqual(commander.validate_generated_command("/replay"), "/replay")
        self.assertEqual(commander.validate_generated_command("/replay example-app"), "/replay example-app")
        self.assertEqual(commander.validate_generated_command("/playback"), "/playback")
        self.assertEqual(commander.validate_generated_command("/playback example-app"), "/playback example-app")
        self.assertEqual(commander.validate_generated_command("/objective example-app"), "/objective example-app")
        self.assertEqual(commander.validate_generated_command('/objective set example-app "Ship onboarding"'), '/objective set example-app "Ship onboarding"')
        self.assertEqual(commander.validate_generated_command("/done example-app"), "/done example-app")
        self.assertEqual(commander.validate_generated_command("/changes"), "/changes")
        self.assertEqual(commander.validate_generated_command("/watch taalam-campaigns"), "/watch taalam-campaigns")
        self.assertEqual(commander.validate_generated_command("/brief"), "/brief")
        self.assertEqual(commander.validate_generated_command("/morning"), "/morning")
        self.assertEqual(commander.validate_generated_command("/next"), "/next")
        self.assertEqual(commander.validate_generated_command("/updates"), "/updates")
        self.assertEqual(commander.validate_generated_command("/mode free"), "/mode free")
        self.assertEqual(commander.validate_generated_command("/tools"), "/tools")
        self.assertEqual(commander.validate_generated_command("/computer codex"), "/computer codex")
        self.assertEqual(commander.validate_generated_command("/browser inspect example.com"), "/browser inspect example.com")
        self.assertEqual(commander.validate_generated_command("/clickup recent campaigns"), "/clickup recent campaigns")
        self.assertEqual(commander.validate_generated_command("/skills playwright"), "/skills playwright")
        self.assertEqual(commander.validate_generated_command("/plugins"), "/plugins")
        self.assertEqual(commander.validate_generated_command("/mcp"), "/mcp")
        self.assertEqual(commander.validate_generated_command("/env"), "/env")
        self.assertEqual(commander.validate_generated_command("/system"), "/system")
        self.assertEqual(commander.validate_generated_command("/clipboard show"), "/clipboard show")
        self.assertEqual(commander.validate_generated_command("/cleanup"), "/cleanup")
        self.assertEqual(commander.validate_generated_command("/open url example.com"), "/open url example.com")
        self.assertEqual(commander.validate_generated_command("/file taalam-campaigns CAMPAIGN_STRATEGY.md"), "/file taalam-campaigns CAMPAIGN_STRATEGY.md")
        self.assertEqual(commander.validate_generated_command("/volume down 5"), "/volume down 5")
        self.assertEqual(commander.validate_generated_command('/start example-app "Fix bugs"'), '/start example-app "Fix bugs"')

    def test_validate_generated_command_blocks_raw_or_unknown_commands(self) -> None:
        with self.assertRaises(RuntimeError):
            commander.validate_generated_command("status")
        with self.assertRaises(RuntimeError):
            commander.validate_generated_command("/run rm -rf")


if __name__ == "__main__":
    unittest.main()
