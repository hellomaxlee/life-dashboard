"""The quote bank: the only words the summary may put under a person's name.

Every entry is a published rendering of the cited passage by a deceased author, written in
ASCII, short enough to show whole with its attribution on the device. The gate accepts a
quotation only when its words match an entry here and the entry's author is the one named;
the prompt offers the model the day's lens entries and nothing else. Adding an entry means
checking it against the printed source first: a smaller true bank beats a larger doubtful one.

`work` is the work and location; `translator` is None for works written in English.
Translators: Long = George Long; Hays = Gregory Hays; Oldfather = W. A. Oldfather;
Gummere = R. M. Gummere; Basore = J. W. Basore; Ross = W. D. Ross; Muller = F. Max Muller;
Legge = James Legge; Watson = Burton Watson; Burnet = John Burnet; Cotton = Charles Cotton.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Quote:
    text: str
    author: str
    work: str
    translator: str | None
    lenses: tuple[str, ...]


AUTHOR_ALIASES: dict[str, tuple[str, ...]] = {
    "Marcus Aurelius": ("marcus aurelius", "aurelius"),
    "Epictetus": ("epictetus",),
    "Seneca": ("seneca",),
    "Aristotle": ("aristotle",),
    "The Dhammapada": ("the dhammapada", "dhammapada"),
    "Lao Tzu": ("lao tzu", "laozi", "lao tse", "lao tsu"),
    "Zhuangzi": ("zhuangzi", "chuang tzu", "zhuang zhou"),
    "Heraclitus": ("heraclitus",),
    "Confucius": ("confucius",),
    "Montaigne": ("michel de montaigne", "montaigne"),
    "Thoreau": ("henry david thoreau", "thoreau"),
    "William James": ("william james",),
    "Emerson": ("ralph waldo emerson", "emerson"),
}

CONTROL = "control"
HABIT = "habit"
LETGO = "non-attachment"
AWARE = "self-awareness"
RESIL = "resilience"
REST = "rest-as-work"
THANKS = "plain gratitude"

MARCUS = "Marcus Aurelius"
DHAMMAPADA = "The Dhammapada"
NE = "Nicomachean Ethics"
PRINCIPLES = "The Principles of Psychology"

QUOTES: tuple[Quote, ...] = (
    Quote(
        "The impediment to action advances action. What stands in the way becomes the way.",
        MARCUS,
        "Meditations 5.20",
        "Hays",
        (RESIL, CONTROL),
    ),
    Quote(
        "In the morning when thou risest unwillingly, let this thought be present - "
        "I am rising to the work of a human being.",
        MARCUS,
        "Meditations 5.1",
        "Long",
        (HABIT, RESIL),
    ),
    Quote(
        "No longer talk at all about the kind of man that a good man ought to be, but be such.",
        MARCUS,
        "Meditations 10.16",
        "Long",
        (HABIT,),
    ),
    Quote(
        "Look within. Within is the fountain of good, and it will ever bubble up, "
        "if thou wilt ever dig.",
        MARCUS,
        "Meditations 7.59",
        "Long",
        (AWARE,),
    ),
    Quote(
        "Do not disturb thyself by thinking of the whole of thy life.",
        MARCUS,
        "Meditations 8.36",
        "Long",
        (LETGO, CONTROL),
    ),
    Quote(
        "I have often wondered how it is that every man loves himself more than all the rest "
        "of men, but yet sets less value on his own opinion of himself than on the opinion "
        "of others.",
        MARCUS,
        "Meditations 12.4",
        "Long",
        (AWARE,),
    ),
    Quote(
        "Let not future things disturb thee, for thou wilt come to them, if it shall be "
        "necessary, having with thee the same reason which now thou usest for present things.",
        MARCUS,
        "Meditations 7.8",
        "Long",
        (CONTROL, LETGO),
    ),
    Quote(
        "The universe is transformation: life is opinion.",
        MARCUS,
        "Meditations 4.3",
        "Long",
        (AWARE, LETGO),
    ),
    Quote(
        "Such as are thy habitual thoughts, such also will be the character of thy mind; "
        "for the soul is dyed by the thoughts.",
        MARCUS,
        "Meditations 5.16",
        "Long",
        (HABIT, AWARE),
    ),
    Quote(
        "If thou art pained by any external thing, it is not this thing that disturbs thee, "
        "but thy own judgment about it. And it is in thy power to wipe out this judgment now.",
        MARCUS,
        "Meditations 8.47",
        "Long",
        (CONTROL,),
    ),
    Quote(
        "Be like the promontory against which the waves continually break, but it stands firm "
        "and tames the fury of the water around it.",
        MARCUS,
        "Meditations 4.49",
        "Long",
        (RESIL,),
    ),
    Quote(
        "Do not act as if thou wert going to live ten thousand years. Death hangs over thee. "
        "While thou livest, while it is in thy power, be good.",
        MARCUS,
        "Meditations 4.17",
        "Long",
        (THANKS, CONTROL),
    ),
    Quote(
        "The art of life is more like the wrestler's art than the dancer's, in respect of "
        "this, that it should stand ready and firm to meet onsets which are sudden and "
        "unexpected.",
        MARCUS,
        "Meditations 7.61",
        "Long",
        (RESIL,),
    ),
    Quote(
        "Of things some are in our power, and others are not.",
        "Epictetus",
        "Enchiridion 1",
        "Long",
        (CONTROL,),
    ),
    Quote(
        "Men are disturbed not by the things which happen, but by the opinions about the things.",
        "Epictetus",
        "Enchiridion 5",
        "Long",
        (CONTROL, AWARE),
    ),
    Quote(
        "Seek not that the things which happen should happen as you wish; but wish the things "
        "which happen to be as they are, and you will have a tranquil flow of life.",
        "Epictetus",
        "Enchiridion 8",
        "Long",
        (LETGO, CONTROL),
    ),
    Quote(
        "Disease is an impediment to the body, but not to the will, unless the will itself "
        "chooses.",
        "Epictetus",
        "Enchiridion 9",
        "Long",
        (RESIL,),
    ),
    Quote(
        "You can be invincible, if you enter into no contest in which it is not in your power "
        "to conquer.",
        "Epictetus",
        "Enchiridion 19",
        "Long",
        (CONTROL,),
    ),
    Quote(
        "No great thing is created suddenly, any more than a bunch of grapes or a fig.",
        "Epictetus",
        "Discourses 1.15",
        "Elizabeth Carter, rev. T. W. Higginson",
        (HABIT, REST),
    ),
    Quote(
        "Every habit and faculty is maintained and increased by the corresponding actions: "
        "the habit of walking by walking, the habit of running by running.",
        "Epictetus",
        "Discourses 2.18",
        "Long",
        (HABIT,),
    ),
    Quote(
        "It is difficulties that show what men are.",
        "Epictetus",
        "Discourses 1.24",
        "Oldfather",
        (RESIL,),
    ),
    Quote(
        "We suffer more often in imagination than in reality.",
        "Seneca",
        "Letters 13.4",
        "Gummere",
        (AWARE, CONTROL),
    ),
    Quote(
        "Lay hold of to-day's task, and you will not need to depend so much upon to-morrow's. "
        "While we are postponing, life speeds by.",
        "Seneca",
        "Letters 1.2",
        "Gummere",
        (HABIT,),
    ),
    Quote(
        "It is not the man who has too little, but the man who craves more, that is poor.",
        "Seneca",
        "Letters 2.6",
        "Gummere",
        (THANKS, LETGO),
    ),
    Quote(
        "You need a change of soul rather than a change of climate.",
        "Seneca",
        "Letters 28.1",
        "Gummere",
        (AWARE,),
    ),
    Quote(
        "When a man does not know what harbour he is making for, no wind is the right wind.",
        "Seneca",
        "Letters 71.3",
        "Gummere",
        (CONTROL, AWARE),
    ),
    Quote(
        "It is not because things are difficult that we do not dare; it is because we do not "
        "dare that things are difficult.",
        "Seneca",
        "Letters 104.26",
        "Gummere",
        (RESIL,),
    ),
    Quote(
        "It is not that we have a short space of time, but that we waste much of it.",
        "Seneca",
        "On the Shortness of Life 1.3",
        "Basore",
        (THANKS,),
    ),
    Quote(
        "The mind must be given relaxation; it will arise better and keener after resting.",
        "Seneca",
        "On Tranquillity of Mind 17.5",
        "Basore",
        (REST,),
    ),
    Quote(
        "For one swallow does not make a summer, nor does one day; and so too one day, or a "
        "short time, does not make a man blessed and happy.",
        "Aristotle",
        f"{NE} 1.7",
        "Ross",
        (HABIT, LETGO),
    ),
    Quote(
        "It is the mark of an educated man to look for precision in each class of things just "
        "so far as the nature of the subject admits.",
        "Aristotle",
        f"{NE} 1.3",
        "Ross",
        (AWARE,),
    ),
    Quote(
        "For the things we have to learn before we can do them, we learn by doing them.",
        "Aristotle",
        f"{NE} 2.1",
        "Ross",
        (HABIT,),
    ),
    Quote(
        "It makes no small difference, then, whether we form habits of one kind or of another "
        "from our very youth; it makes a very great difference, or rather all the difference.",
        "Aristotle",
        f"{NE} 2.1",
        "Ross",
        (HABIT,),
    ),
    Quote(
        "Both excessive and defective exercise destroys the strength.",
        "Aristotle",
        f"{NE} 2.2",
        "Ross",
        (REST, AWARE),
    ),
    Quote(
        "For in everything it is no easy task to find the middle.",
        "Aristotle",
        f"{NE} 2.9",
        "Ross",
        (AWARE, RESIL),
    ),
    Quote(
        "We need relaxation because we cannot work continuously.",
        "Aristotle",
        f"{NE} 10.6",
        "Ross",
        (REST,),
    ),
    Quote(
        "We are busy that we may have leisure, and make war that we may live in peace.",
        "Aristotle",
        f"{NE} 10.7",
        "Ross",
        (REST, THANKS),
    ),
    Quote(
        "All that we are is the result of what we have thought: it is founded on our "
        "thoughts, it is made up of our thoughts.",
        DHAMMAPADA,
        "Dhammapada 1",
        "Muller",
        (HABIT, AWARE),
    ),
    Quote(
        "By rousing himself, by earnestness, by restraint and control, the wise man may make "
        "for himself an island which no flood can overwhelm.",
        DHAMMAPADA,
        "Dhammapada 25",
        "Muller",
        (RESIL, CONTROL),
    ),
    Quote(
        "As a fletcher makes straight his arrow, a wise man makes straight his trembling and "
        "unsteady thought, which is difficult to guard, difficult to hold back.",
        DHAMMAPADA,
        "Dhammapada 33",
        "Muller",
        (AWARE, CONTROL),
    ),
    Quote(
        "It is good to tame the mind, which is difficult to hold in and flighty, rushing "
        "wherever it listeth; a tamed mind brings happiness.",
        DHAMMAPADA,
        "Dhammapada 35",
        "Muller",
        (AWARE,),
    ),
    Quote(
        "As a solid rock is not shaken by the wind, wise people falter not amidst blame and "
        "praise.",
        DHAMMAPADA,
        "Dhammapada 81",
        "Muller",
        (RESIL, LETGO),
    ),
    Quote(
        "If one man conquer in battle a thousand times thousand men, and if another conquer "
        "himself, he is the greatest of conquerors.",
        DHAMMAPADA,
        "Dhammapada 103",
        "Muller",
        (CONTROL, RESIL),
    ),
    Quote(
        "Even by the falling of water-drops a water-pot is filled; the wise man becomes full "
        "of good, even if he gather it little by little.",
        DHAMMAPADA,
        "Dhammapada 122",
        "Muller",
        (HABIT,),
    ),
    Quote(
        "Self is the lord of self, who else could be the lord? With self well subdued, a man "
        "finds a lord such as few can find.",
        DHAMMAPADA,
        "Dhammapada 160",
        "Muller",
        (CONTROL,),
    ),
    Quote(
        "Victory breeds hatred, for the conquered is unhappy. He who has given up both "
        "victory and defeat, he, the contented, is happy.",
        DHAMMAPADA,
        "Dhammapada 201",
        "Muller",
        (LETGO,),
    ),
    Quote(
        "Health is the greatest of gifts, contentedness the best riches; trust is the best of "
        "relationships, Nirvana the highest happiness.",
        DHAMMAPADA,
        "Dhammapada 204",
        "Muller",
        (THANKS,),
    ),
    Quote(
        "Let a wise man blow off the impurities of his self, as a smith blows off the "
        "impurities of silver one by one, little by little, and from time to time.",
        DHAMMAPADA,
        "Dhammapada 239",
        "Muller",
        (HABIT,),
    ),
    Quote(
        "It is better to leave a vessel unfilled, than to attempt to carry it when it is full.",
        "Lao Tzu",
        "Tao Te Ching 9",
        "Legge",
        (REST, LETGO),
    ),
    Quote(
        "When the work is done, and one's name is becoming distinguished, to withdraw into "
        "obscurity is the way of Heaven.",
        "Lao Tzu",
        "Tao Te Ching 9",
        "Legge",
        (LETGO, REST),
    ),
    Quote(
        "He who knows other men is discerning; he who knows himself is intelligent. He who "
        "overcomes others is strong; he who overcomes himself is mighty.",
        "Lao Tzu",
        "Tao Te Ching 33",
        "Legge",
        (AWARE, CONTROL),
    ),
    Quote(
        "A journey of a thousand miles begins with a single step.",
        "Lao Tzu",
        "Tao Te Ching 64",
        "common English rendering; Legge has 'the journey of a thousand li commenced with a "
        "single step'",
        (HABIT, RESIL),
    ),
    Quote(
        "Your life has a limit but knowledge has none. If you use what is limited to pursue "
        "what has no limit, you will be in danger.",
        "Zhuangzi",
        "Zhuangzi 3",
        "Watson",
        (REST, LETGO),
    ),
    Quote(
        "The Perfect Man uses his mind like a mirror - going after nothing, welcoming "
        "nothing, responding but not storing.",
        "Zhuangzi",
        "Zhuangzi 7",
        "Watson",
        (LETGO, AWARE),
    ),
    Quote(
        "The fish trap exists because of the fish; once you've gotten the fish, you can "
        "forget the trap.",
        "Zhuangzi",
        "Zhuangzi 26",
        "Watson",
        (LETGO, THANKS),
    ),
    Quote(
        "You cannot step twice into the same rivers; for fresh waters are ever flowing in "
        "upon you.",
        "Heraclitus",
        "Fragments DK B12 (Burnet 41-42)",
        "Burnet",
        (LETGO,),
    ),
    Quote(
        "The way up and the way down is one and the same.",
        "Heraclitus",
        "Fragments DK B60 (Burnet 69)",
        "Burnet",
        (LETGO, RESIL),
    ),
    Quote(
        "It rests by changing.",
        "Heraclitus",
        "Fragments DK B84a (Burnet 83)",
        "Burnet",
        (REST,),
    ),
    Quote(
        "It is sickness that makes health pleasant; evil, good; hunger, plenty; weariness, rest.",
        "Heraclitus",
        "Fragments DK B111 (Burnet 104)",
        "Burnet",
        (THANKS, REST),
    ),
    Quote(
        "Man's character is his fate.",
        "Heraclitus",
        "Fragments DK B119 (Burnet 121)",
        "Burnet",
        (HABIT,),
    ),
    Quote(
        "When you know a thing, to hold that you know it; and when you do not know a thing, "
        "to allow that you do not know it; this is knowledge.",
        "Confucius",
        "Analects 2.17",
        "Legge",
        (AWARE,),
    ),
    Quote(
        "They who know the truth are not equal to those who love it, and they who love it "
        "are not equal to those who delight in it.",
        "Confucius",
        "Analects 6.18 (Legge's numbering)",
        "Legge",
        (THANKS, HABIT),
    ),
    Quote(
        "To go beyond is as wrong as to fall short.",
        "Confucius",
        "Analects 11.15 (Legge's numbering)",
        "Legge",
        (REST, AWARE),
    ),
    Quote(
        "Desire to have things done quickly prevents their being done thoroughly.",
        "Confucius",
        "Analects 13.17",
        "Legge",
        (HABIT, REST),
    ),
    Quote(
        "The superior man is modest in his speech, but exceeds in his actions.",
        "Confucius",
        "Analects 14.29 (Legge's numbering)",
        "Legge",
        (HABIT,),
    ),
    Quote(
        "What the superior man seeks, is in himself. What the mean man seeks, is in others.",
        "Confucius",
        "Analects 15.20 (Legge's numbering)",
        "Legge",
        (CONTROL,),
    ),
    Quote(
        "The greatest thing in the world is for a man to know that he is his own.",
        "Montaigne",
        "Essays 1.39, Of Solitude",
        "Cotton",
        (CONTROL,),
    ),
    Quote(
        "When I dance, I dance; when I sleep, I sleep.",
        "Montaigne",
        "Essays 3.13, Of Experience",
        "Cotton",
        (THANKS, REST),
    ),
    Quote(
        "Our life is frittered away by detail.",
        "Thoreau",
        "Walden, Where I Lived, and What I Lived For",
        None,
        (AWARE, LETGO),
    ),
    Quote(
        "If a man does not keep pace with his companions, perhaps it is because he hears a "
        "different drummer. Let him step to the music which he hears, however measured or "
        "far away.",
        "Thoreau",
        "Walden, Conclusion",
        None,
        (AWARE, LETGO),
    ),
    Quote(
        "Only that day dawns to which we are awake.",
        "Thoreau",
        "Walden, Conclusion",
        None,
        (THANKS,),
    ),
    Quote(
        "Habit is thus the enormous fly-wheel of society, its most precious conservative agent.",
        "William James",
        f"{PRINCIPLES}, ch. 4 (Habit)",
        None,
        (HABIT,),
    ),
    Quote(
        "The great thing, then, in all education, is to make our nervous system our ally "
        "instead of our enemy.",
        "William James",
        f"{PRINCIPLES}, ch. 4 (Habit)",
        None,
        (HABIT, REST),
    ),
    Quote(
        "Keep the faculty of effort alive in you by a little gratuitous exercise every day.",
        "William James",
        f"{PRINCIPLES}, ch. 4 (Habit)",
        None,
        (RESIL, HABIT),
    ),
    Quote(
        "My experience is what I agree to attend to.",
        "William James",
        f"{PRINCIPLES}, ch. 11 (Attention)",
        None,
        (AWARE, CONTROL),
    ),
    Quote(
        "The art of being wise is the art of knowing what to overlook.",
        "William James",
        f"{PRINCIPLES}, ch. 22 (Reasoning)",
        None,
        (LETGO, AWARE),
    ),
    Quote(
        "A foolish consistency is the hobgoblin of little minds, adored by little statesmen "
        "and philosophers and divines.",
        "Emerson",
        "Essays: First Series, Self-Reliance",
        None,
        (LETGO,),
    ),
    Quote(
        "Nothing can bring you peace but yourself.",
        "Emerson",
        "Essays: First Series, Self-Reliance",
        None,
        (CONTROL,),
    ),
    Quote(
        "To finish the moment, to find the journey's end in every step of the road, to live "
        "the greatest number of good hours, is wisdom.",
        "Emerson",
        "Essays: Second Series, Experience",
        None,
        (THANKS, LETGO),
    ),
    Quote(
        "We live amid surfaces, and the true art of life is to skate well on them.",
        "Emerson",
        "Essays: Second Series, Experience",
        None,
        (RESIL, THANKS),
    ),
    Quote(
        "Write it on your heart that every day is the best day in the year.",
        "Emerson",
        "Society and Solitude, Works and Days",
        None,
        (THANKS,),
    ),
)


def display(quote: Quote) -> str:
    """The quote as the device shows it: straight double quotes, a hyphen, the author."""
    return f'"{quote.text}" - {quote.author}'


def for_lens(lens: str) -> tuple[Quote, ...]:
    return tuple(q for q in QUOTES if lens in q.lenses)
