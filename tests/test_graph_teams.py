from desktop_agent.graph.client import find_matching_chats, other_member
from desktop_agent.tools.people import names_match


def test_graph_matches_split_display_name():
    assert names_match("Janapati Thanusree", "Janapati Thanu Sree") == 100


def test_find_matching_chats_picks_existing_member(monkeypatch):
    me = "me-1"
    chats = [
        {
            "id": "chat-janapati",
            "members": [
                {"userId": "me-1", "displayName": "Me", "email": "me@contoso.com"},
                {
                    "userId": "u-2",
                    "displayName": "Janapati Thanu Sree",
                    "email": "janapati.thanusree@contoso.com",
                },
            ],
        }
    ]
    monkeypatch.setattr("desktop_agent.graph.client._pages", lambda token, path, **kwargs: chats)
    hits = find_matching_chats("token", "Janapati Thanusree", me)
    assert hits
    assert hits[0]["chat_id"] == "chat-janapati"
    assert hits[0]["email"] == "janapati.thanusree@contoso.com"


def test_other_member_skips_self():
    chat = {
        "members": [
            {"userId": "me-1", "displayName": "Me"},
            {"userId": "u-2", "displayName": "Megha S D", "email": "megha@contoso.com"},
        ]
    }
    member = other_member(chat, "me-1")
    assert member["displayName"] == "Megha S D"
