import json
import unittest
from teach import game

class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.db = game.connect(':memory:')
        self.uid = game.user_id(self.db, 'Alumno')
        self.db.execute("INSERT INTO problems (id,type,tier,correct_value,steps,qwen_correct,guide) VALUES ('p','calculo',2,'6','[]',0,?)", (json.dumps(["Pista"]),))
        self.db.commit()
        game._TYPE_IDS.clear()

    def tearDown(self):
        self.db.close()

    def play(self):
        return game.start_play(self.db,self.uid,'p','solve')

    def test_attempt_penalties_and_history(self):
        for fails, expected in enumerate((20,15,10,5,5)):
            p=self.play()
            for _ in range(fails):
                self.assertFalse(game.answer(self.db,p,self.uid,None,'7')['right'])
            r=game.answer(self.db,p,self.uid,None,'6')
            self.assertEqual(r['points'],expected)
        self.assertEqual(game.stats(self.db,self.uid)['types']['calculo']['points'],20)
        self.assertEqual(game.history(self.db,self.uid)['plays'][-1]['total'],20)

    def test_combined_factors_and_bad_input(self):
        p=self.play()
        with self.assertRaises(ValueError):
            game.answer(self.db,p,self.uid,None,'hello')
        self.assertEqual(game._play(self.db,p,self.uid)[2],0)
        game.help_rung(self.db,p,self.uid)
        game.answer(self.db,p,self.uid,None,'7')
        r=game.answer(self.db,p,self.uid,None,'6')
        self.assertEqual(r['points'],11.25)
        self.assertEqual(game.stats(self.db,self.uid)['types']['calculo']['points'],11.25)
        self.assertEqual(game.history(self.db,self.uid)['plays'][-1]['total'],11.25)

    def test_independent_streaks_and_resets(self):
        p=self.play(); game.answer(self.db,p,self.uid,None,'6')
        p=self.play(); game.answer(self.db,p,self.uid,None,'7')
        self.assertEqual(game.streaks(self.db,self.uid)['first_try'],{'current':0,'best':1})
        game.answer(self.db,p,self.uid,None,'6')
        self.assertEqual(game.streaks(self.db,self.uid)['no_help'],{'current':2,'best':2})
        p=self.play(); game.help_rung(self.db,p,self.uid)
        self.assertEqual(game.streaks(self.db,self.uid)['no_help']['current'],0)
        game.answer(self.db,p,self.uid,None,'6')
        self.assertEqual(game.streaks(self.db,self.uid)['first_try']['current'],1)
        p=self.play(); game.give_up(self.db,p,self.uid)
        for s in game.streaks(self.db,self.uid).values(): self.assertEqual(s['current'],0)
        p=self.play() # untouched does not change records
        self.assertEqual(game.streaks(self.db,self.uid)['no_help']['best'],2)

    def test_user_isolation_and_finished_play(self):
        p=self.play(); game.answer(self.db,p,self.uid,None,'6')
        other=game.user_id(self.db,'Otro')
        self.assertEqual(game.streaks(self.db,other)['first_try']['best'],0)
        with self.assertRaises(ValueError): game.answer(self.db,p,self.uid,None,'6')

if __name__ == '__main__': unittest.main()
