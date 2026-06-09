from database.problem_change_queue import ProblemChangeQueueController


class _FakeCursor:
    def __init__(self, responses):
        self._responses = list(responses)
        self.executed = []

    def execute(self, query, params):
        self.executed.append((" ".join(query.split()), params))

    def fetchone(self):
        if self._responses:
            return self._responses.pop(0)
        return None


def _controller():
    return ProblemChangeQueueController.__new__(ProblemChangeQueueController)


def test_find_server_problem_does_not_fallback_across_instances():
    controller = _controller()
    cur = _FakeCursor([None])

    row = controller._find_server_problem(
        cur,
        numero_original=1,
        archivo_origen=r"E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\ALGEBRA_OTROS_PDFS_UNIDOS.pdf",
        libro_codigo="vesalius-algebra-temas",
        codigo_instancia="s02_radicales",
    )

    assert row is None
    assert len(cur.executed) == 1
    assert "codigo_instancia = %s" in cur.executed[0][0]


def test_find_server_problem_uses_book_scoped_fallback_without_instance():
    controller = _controller()
    cur = _FakeCursor([(4721, 1)])

    row = controller._find_server_problem(
        cur,
        numero_original=12,
        archivo_origen=r"E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\ALGEBRA_OTROS_PDFS_UNIDOS.pdf",
        libro_codigo="vesalius-algebra-temas",
        codigo_instancia="",
    )

    assert row == (4721, 1)
    assert len(cur.executed) == 1
    assert "libro_codigo = %s" in cur.executed[0][0]
    assert "archivo_origen = %s" in cur.executed[0][0]


def test_find_server_problem_falls_back_to_source_when_context_missing():
    controller = _controller()
    cur = _FakeCursor([(100, 3)])

    row = controller._find_server_problem(
        cur,
        numero_original=7,
        archivo_origen="shared.pdf",
        libro_codigo="",
        codigo_instancia="",
    )

    assert row == (100, 3)
    assert len(cur.executed) == 1
    assert "archivo_origen = %s" in cur.executed[0][0]
