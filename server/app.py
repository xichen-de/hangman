import datetime
import uuid

from flask import Flask, g
from flask_cors import CORS
from flask_restx import Namespace, Resource, Api
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from server.hangman_orm import Usage, User, Game
from server.util import get_config

games_api = Namespace('games', description='Creating and playing games')
app = Flask(__name__)


@games_api.route('')
class Games(Resource):
    valid_langs = ('en', 'es', 'fr')

    def post(self):
        # check input is valid
        if not (games_api.payload and
                'username' in games_api.payload and
                'language' in games_api.payload):
            games_api.abort(400, 'New game POST requires username and language')
        lang = games_api.payload['language']
        name = games_api.payload['username']
        user_id = str(uuid.uuid3(uuid.NAMESPACE_URL, name))
        if lang not in self.valid_langs:
            return {'message': 'New game POST language must be from ' +
                               ', '.join(Games.valid_langs)}, 400

        # if user does not exist, create user; get user id
        user = g.games_db.query(User).filter(User.user_id == user_id).one_or_none()
        if user is None:
            user = User(
                user_id=user_id,
                user_name=name,
                first_time=datetime.datetime.now(),
            )
            g.games_db.add(user)
            g.games_db.commit()
            user = g.games_db.query(User).filter(User.user_name == name).one()
        user.game_started(lang)

        # select a usage example
        usage = g.usage_db.query(Usage).filter(
            Usage.language == lang
        ).order_by(func.random()).first()

        # create the new game
        new_game_id = str(uuid.uuid4())
        new_game = Game(
            game_id=new_game_id,
            player=user.user_id,
            usage_id=usage.usage_id,
            bad_guesses=0,
            reveal_word='_' * len(usage.secret_word),
            start_time=datetime.datetime.now()
        )
        g.games_db.add(new_game)
        g.games_db.commit()

        return {'message': 'success', 'game_id': new_game_id}


@games_api.route('/<game_id>')
class OneGame(Resource):
    def get(self, game_id):
        # check input is valid
        game = g.games_db.query(Game).filter(Game.game_id == game_id).one_or_none()

        # if game does not exist, produce error code
        if game is None:
            games_api.abort(404, 'Game with id {} does not exist'.format(game_id))

        # get usage record because it contains the language and usage example
        usage = g.usage_db.query(Usage).filter(Usage.usage_id == game.usage_id).one()

        # return game state
        game_dict = game.to_dict()
        game_dict['usage'] = usage.usage.format(word='_' * len(usage.secret_word))
        game_dict['lang'] = usage.language
        game_dict['source'] = usage.source

        return game_dict

    def put(self, game_id):
        # check input is valid; return error if game non-existent or inactive
        game = g.games_db.query(Game).filter(Game.game_id == game_id).one_or_none()
        if game is None:
            games_api.abort(404, 'Game with id {} does not exist'.format(game_id))
        if game.result() != 'active':
            games_api.abort(403, 'Game with id {} is over'.format(game_id))
        if ('letter' not in games_api.payload or
                not games_api.payload['letter'].isalpha() or
                len(games_api.payload['letter']) != 1):
            games_api.abort(400, 'PUT requires one alphabetic character in "letter" field')
        letter = games_api.payload['letter'].lower()

        # update game state according to guess
        if letter in game.guessed:  # check for repeated guess
            games_api.abort(403, 'Letter {} was already guessed'.format(letter))
        game.guessed = game.guessed + letter
        usage = g.usage_db.query(Usage).filter(Usage.usage_id == game.usage_id).one()
        if letter in usage.secret_word.lower():
            game.reveal_word = ''.join([l if l.lower() in game.guessed else '_'
                                        for l in usage.secret_word])
        else:
            game.bad_guesses += 1

        # if game is over, update the user record
        outcome = game.result()
        if outcome != 'active':
            user = g.games_db.query(User).filter(User.user_id == game.player).one()
            game.end_time = datetime.datetime.now()
            user.game_ended(outcome, game.end_time - game.start_time)

            # return the modified game state
        game_dict = game.to_dict()
        game_dict['usage'] = usage.usage.format(word='_' * len(usage.secret_word))
        game_dict['lang'] = usage.language
        game_dict['source'] = usage.source
        if outcome != 'active':
            game_dict['secret_word'] = usage.secret_word

        g.games_db.commit()

        return game_dict

    def delete(self, game_id):
        """End the game, delete the record"""
        return {'message': 'Game DELETE under construction'}


@app.before_request
def init_db():
    """Initialize db by creating the global db_session"""
    if not hasattr(g, 'usage_db'):
        db_usage = create_engine(app.config['DB_USAGE'])
        g.usage_db = sessionmaker(db_usage)()

    if not hasattr(g, 'games_db'):
        db_games = create_engine(app.config['DB_GAMES'])
        g.games_db = sessionmaker(db_games)()


@app.teardown_request
def close_db(exception):
    """Close down db connection; same one cannot be used b/w threads"""
    if hasattr(g, 'usage_db'):
        g.usage_db.close()
        _ = g.pop('usage_db')

    if hasattr(g, 'games_db'):
        g.games_db.close()
        _ = g.pop('games_db')


if __name__ == '__main__':
    app.config.update(get_config(app.config['ENV'],
                                 app.open_resource('config.yaml')))
    CORS(app)  # Cross-origin resource sharing
    api = Api(app)
    api.add_namespace(games_api, path='/api/games')
    app.run(debug=True)
