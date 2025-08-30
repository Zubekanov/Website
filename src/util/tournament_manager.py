import string
import random
from util.psql_manager import PSQLClient
from util.tournament_player import Tournament_User

client = PSQLClient()

class Tournament:
    def __init__(self, uid=None):
        self.uid = uid
        self.contents = client.get_rows_by_conditions("tournament", {"uid": uid})[0]

    @classmethod
    def create(cls):
        tournament = client.insert_row("tournament", {
            "uid": None,
            "display_name": cls._generate_temporary_tournament_display_name(),
            "share_code": cls._generate_unique_share_code()
        }, returning=["uid"])
        if tournament:
            return cls(uid=tournament[0]["uid"])
        return None

    @classmethod
    def load_from_id(cls, tournament_id):
        tournament = client.get_rows_by_conditions("tournament", {"id": tournament_id})
        if tournament:
            return cls(uid=tournament[0]["uid"])
        return None

    @staticmethod
    def _generate_unique_share_code(length=5, max_attempts=20):
        alphabet = string.ascii_lowercase

        for _ in range(max_attempts):
            code = ''.join(random.choices(alphabet, k=length))
            # check if it exists already
            rows = client.get_rows_by_conditions("tournament", {"share_code": code})
            if not rows:
                return code
        raise RuntimeError(f"Failed to generate a unique share_code after {max_attempts} attempts")

    @staticmethod
    def _generate_temporary_tournament_display_name(length=5, max_attempts=20):
        for _ in range(max_attempts):
            suffix = str(random.randint(10**(length-1), 10**length - 1))
            name = "Tournament " + suffix
            rows = client.get_rows_by_conditions("tournament", {"display_name": name})
            if not rows:
                return name
        raise RuntimeError(f"Failed to generate a unique display_name after {max_attempts} attempts")

def build_html():
    pass