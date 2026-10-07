"""Terminal conversation check: humans supply actions, state persists."""
from pathlib import Path
import tempfile
import unittest
from adventure_cli import chat,Adventure,FakeModel,FixedRandom


class Checks(unittest.TestCase):
    def test_chat_commands_and_human_action(self):
        with tempfile.TemporaryDirectory() as folder:
            model=FakeModel()
            game=Adventure(Path(folder)/'test.sqlite3',model,rng=FixedRandom())
            inputs=iter(['状态','冒险','用热水融冰，把它赶下桥。','背包','退出'])
            outputs=[]
            chat(game,'human',input_fn=lambda _:next(inputs),output=outputs.append)
            self.assertEqual([s for s,_ in model.calls],['event','evaluate','narrate'])
            evaluated=next(c for s,c in model.calls if s=='evaluate')
            self.assertEqual(evaluated['action'],'用热水融冰，把它赶下桥。')
            self.assertTrue(any('水之剑' in line for line in outputs))
            self.assertEqual(game.status('human')['state']['level'],1)
            reopened=Adventure(game.path,model)
            again=iter(['状态','退出'])
            chat(reopened,'human',input_fn=lambda _:next(again),output=outputs.append)
            self.assertTrue(any('Lv.1' in line for line in outputs))
            self.assertEqual(len(model.calls),3)


if __name__=='__main__':
    unittest.main(verbosity=2)
