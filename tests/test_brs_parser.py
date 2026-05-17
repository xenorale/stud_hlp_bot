from utils.brs_parser import parse_lessons_html

def test_parse_lessons_stats_empty():
    html = "<html><body><table><tbody></tbody></table></body></html>"
    stats = parse_lessons_html(html)
    assert stats.total == 0
    assert stats.attended == 0
    assert stats.skipped == 0

def test_parse_lessons_stats_valid():
    html = """
    <html><body><table><tbody>
        <tr>
            <td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td> + </td>
        </tr>
        <tr>
            <td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td> - </td>
        </tr>
        <tr>
            <td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td> - </td>
        </tr>
        <tr>
            <td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td> </td>
        </tr>
    </tbody></table></body></html>
    """
    stats = parse_lessons_html(html)
    assert stats.total == 4
    assert stats.skipped == 2
    assert stats.attended == 1

