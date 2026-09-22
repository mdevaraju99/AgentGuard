from desktop_agent.tools.people import names_match, pick_person, score_person


def test_thanusree_matches_split_name():
    assert names_match("Janapati Thanusree", "Janapati Thanu Sree") == 100


def test_score_full_name_match():
    assert score_person("Janapati Thanusree", "janapati.thanusree@contoso.com", "Janapati Thanusree") == 100


def test_pick_unique_gal_person():
    chosen, matches = pick_person(
        "Janapati Thanusree",
        [
            {
                "name": "Janapati Thanusree",
                "email": "janapati.thanusree@contoso.com",
                "source": "gal",
            }
        ],
    )
    assert chosen is not None
    assert chosen["email"] == "janapati.thanusree@contoso.com"
    assert matches


def test_pick_ambiguous_requires_choice():
    chosen, matches = pick_person(
        "Janapati",
        [
            {"name": "Janapati Thanusree", "email": "a@contoso.com", "source": "gal"},
            {"name": "Janapati Rao", "email": "b@contoso.com", "source": "gal"},
        ],
    )
    assert chosen is None
    assert len(matches) == 2
