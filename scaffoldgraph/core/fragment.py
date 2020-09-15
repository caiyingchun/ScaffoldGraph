"""
scaffoldgraph.core.fragment
"""

from abc import ABC, abstractmethod

from loguru import logger

from rdkit import RDLogger
from rdkit.Chem import (
    RWMol,
    MolToSmiles,
    rdmolops,
    SanitizeMol,
    GetMolFrags,
    BondType,
    CHI_UNSPECIFIED,
    SANITIZE_ALL,
    SANITIZE_CLEANUP,
    SANITIZE_CLEANUPCHIRALITY,
    SANITIZE_FINDRADICALS,
)
from rdkit.Chem.Scaffolds import MurckoScaffold

from scaffoldgraph.core.scaffold import Scaffold

rdlogger = RDLogger.logger()


class Fragmenter(ABC):
    """Abstract base class for scaffold fragmentation methods.

    Fragmenters should be designed to be used for generating
    scaffold graphs. subclasses may use attributes to store an
    internal state or property used during fragmentation.

    Subclasses should define the fragment method which takes a
    scaffold ``scafooldgraph.core.Scaffold`` as an argument
    and returns the next set of parent scaffolds after
    fragmentation of the child scaffold. Subclasses may also
    define an __init__ method to supply any arguments which may
    be used during fragmentation. Note: it is expected that
    the fragment method return scaffolds as
    scaffoldgraph.core.Scaffold objects.

    The __call__ method can be used as an alias to the fragment
    function.

    """
    def __call__(self, scaffold):
        return self.fragment(scaffold)

    @abstractmethod
    def fragment(self, scaffold):
        """Subclasses should implement this method.

        Parameters
        ----------
        scaffold : scaffoldgraph.core.Scaffold
            Child scaffold to be fragmented.

        Returns
        -------
        list
            This method should return the next set of parent scaffolds
            after fragmentation of the child scaffold.

        """
        raise NotImplementedError()

    def __repr__(self):
        return '<{_cls} at {address}>'.format(
            _cls=self.__class__.__name__,
            address=hex(id(self))
        )


class MurckoRingFragmenter(Fragmenter):
    """
    A Fragmenter class for the removal of peripheral rings from a
    Murcko scaffold.

    This fragmenter is hierarchical, returning only parent scaffolds
    that are one hierarchy below the supplied child scaffold (i.e.
    one ring less).

    Notes
    -----
    This paticular implementation will also fragment fused ring
    systems. If this behaviour is not desired the
    ``MurckoRingSystemFragmenter`` may be used instead.

    See Also
    --------
    scaffoldgraph.core.fragment.MurckoRingSystemFragmenter

    """
    def __init__(self, use_scheme_4=False):
        """
        Parameters
        ----------
        use_scheme_4 : bool
            if True use scheme 4 from the paper:
            `The Scaffold Tree − Visualization of the Scaffold Universe
            by Hierarchical Scaffold Classification.` This scheme should
            be used when generating scaffold trees with the original
            prioritization rules.

        Notes
        -----
        Scheme 4 (`description taken from paper`):
            The fusion bond connecting a three-membered ring with other
            rings is converted into a double bond. This rule is intended
            to deal with epoxides and aziridines. This rule treats such
            systems as functional groups which are removed beforehand,
            rather than as rings. This reflects the situation that epoxides
            are usually generated by the oxidation of a double bond, and
            also many natural products exist often in forms with and
            without epoxidized double bonds.

        """
        super(MurckoRingFragmenter, self).__init__()
        self.use_scheme_4 = use_scheme_4

    def fragment(self, scaffold):
        """Fragment a scaffold into its next set of Murcko fragments.

        Parameters
        ----------
        scaffold : scaffoldgraph.core.Scaffold
            Child scaffold to be fragmented.

        Returns
        -------
        list
            A list of parent scaffolds representing the next hierarchy.

        """
        parents = []  # container for parent scaffolds
        rings = scaffold.rings  # ring information

        for rix, ring in enumerate(rings):  # Loop through all rings and remove
            edit = RWMol(scaffold.mol)  # Editable molecule

            # Collect all removable atoms in the molecule
            remove_atoms = set()
            for index, atom in zip(ring.aix, ring.atoms):
                if rings.info.NumAtomRings(index) == 1:
                    if atom.GetDegree() > 2:  # Evoke linker collection
                        collect_linker_atoms(edit.GetAtomWithIdx(index), remove_atoms)
                    else:  # Add ring atom to removable set
                        remove_atoms.add(index)
                else:  # Atom is shared between multiple rings
                    correct_atom_props(edit.GetAtomWithIdx(index))

            # Collect removable bonds (this needs to be done to prevent the case where when deleting
            # a ring two atoms belonging to the same bond are also part of separate other rings.
            # This bond must be broken to prevent an incorrect output)
            remove_bonds = set()
            for bix in {x for x in ring.bix if rings.info.NumBondRings(x) == 1}:
                bond = edit.GetBondWithIdx(bix)
                b_x, b_y = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
                if b_x not in remove_atoms and b_y not in remove_atoms:
                    remove_bonds.add((b_x, b_y))
                    correct_atom_props(edit.GetAtomWithIdx(b_x))
                    correct_atom_props(edit.GetAtomWithIdx(b_y))

            # Scheme 4 (scaffold tree rule)
            if self.use_scheme_4 is not False and len(ring) == 3:
                atomic_nums = [a.GetAtomicNum() for a in ring.atoms]
                if len([a for a in atomic_nums if a != 1 and a != 6]) == 1:
                    shared = {x for x in ring.bix if rings.info.NumBondRings(x) > 1}
                    if len(shared) == 1:
                        bond = edit.GetBondWithIdx(shared.pop())
                        bond.SetBondType(BondType.DOUBLE)

            # Remove collected atoms and bonds
            for bix in remove_bonds:
                edit.RemoveBond(*bix)
            for aix in sorted(remove_atoms, reverse=True):
                edit.RemoveAtom(aix)

            # Add new parent scaffolds to parent list
            for parent in get_scaffold_frags(edit):
                if parent.rings.count == len(rings) - 1:
                    parent.removed_ring_idx = rix
                    parents.append(parent)

        return parents


class MurckoRingSystemFragmenter(Fragmenter):
    """A Fragmenter class for the removal of peripheral ring systems from a
    Murcko scaffold.

    This fragmenter is hierarchical, returning only parent scaffolds
    that are one hierarchy below the supplied child scaffold. In this
    case the hierarchy below is one ring system less rather than one
    ring less.

    Notes
    -----
    This paticular implementation will not fragment fused ring
    systems. If this behaviour is not desired the
    ``MurckoRingFragmenter`` may be used instead.

    See Also
    --------
    scaffoldgraph.core.fragment.MurckoRingFragmenter

    """
    def __init__(self):
        super(MurckoRingSystemFragmenter, self).__init__()

    def fragment(self, scaffold):
        """Fragment a scaffold into its next set of Murcko fragments.

        Parameters
        ----------
        scaffold : scaffoldgraph.core.Scaffold
            Child scaffold to be fragmented.

        Returns
        -------
        list
            A list of parent scaffolds representing the next hierarchy.

        """
        parents = []
        rings = scaffold.ring_systems  # ring system information
        info = scaffold.rings.info

        if rings.count == 1:
            return []
        for rix, ring in enumerate(rings):
            edit = RWMol(scaffold.mol)
            remove_atoms = set()
            for index, atom in zip(ring.aix, ring.atoms):
                if info.NumAtomRings(index) == 1:
                    if atom.GetDegree() > 2:  # Evoke linker collection
                        collect_linker_atoms(edit.GetAtomWithIdx(index), remove_atoms)
                    else:
                        remove_atoms.add(index)
                else:
                    remove_atoms.add(index)

            for aix in sorted(remove_atoms, reverse=True):
                edit.RemoveAtom(aix)

            for parent in get_scaffold_frags(edit):
                if parent.ring_systems.count == len(rings) - 1:
                    parent.removed_ring_idx = rix
                    parents.append(parent)

        return parents


def collect_linker_atoms(origin, remove_atoms, include_origin=True):
    """Collect atoms that are part of a linker.

    This function is primarily used during molecular fragmentation.
    Linker atoms are added to the `remove_atoms` set supplied. The
    funtion also returns any ring attachment points encountered.

    Parameters
    ----------
    origin : rdkit.Chem.rdchem.Atom
        Origin atom from where the linker will be collected.
    remove_atoms : set
        A set to which linker atoms indexes will be added may
        or may not contain other atom indexes.
    include_origin : bool
        If True add the origin atoms index to the `remove_atoms`
        set supplied.

    Returns
    -------
    set
        The returned set contains any ring attachment points
        encountered. The linker atoms are added to the
        `remove_atoms` set that is supplied.

    """
    visited = set()  # Visited bond indexes
    ring_attachments = set()  # Linker ring attachments

    def collect(origin_atom):

        for bond in origin_atom.GetBonds():
            bond_id = bond.GetIdx()
            if bond_id in visited or bond.IsInRing():
                continue

            other_atom = bond.GetOtherAtom(origin_atom)
            other_degree = other_atom.GetDegree()

            if other_degree == 1:  # Terminal side-chain
                remove_atoms.add(origin_atom.GetIdx())
                remove_atoms.add(other_atom.GetIdx())
                correct_atom_props(origin_atom)
                visited.add(bond_id)

            elif other_degree == 2:  # Two neighboring atoms (remove)
                remove_atoms.add(origin_atom.GetIdx())
                visited.add(bond_id)
                collect(other_atom)

            elif other_degree > 2:  # Branching point

                # Determine number of non-terminal branches
                non_terminal_branches = 0
                for neighbor in other_atom.GetNeighbors():
                    if neighbor.GetDegree() != 1:
                        non_terminal_branches += 1

                if non_terminal_branches < 3:  # Continue with deletion
                    remove_atoms.add(origin_atom.GetIdx())
                    visited.add(bond_id)
                    collect(other_atom)

                else:  # Branching point links two rings
                    # Test for exolinker double bond
                    if not bond.GetBondType() == BondType.DOUBLE:
                        remove_atoms.add(origin_atom.GetIdx())
                        correct_atom_props(other_atom)
                        visited.add(bond_id)
                    if other_atom.IsInRing():
                        ring_attachments.add(other_atom.GetIdx())

    # Linker is recursively collected
    # Linker atoms are added to the existing set 'remove_atoms'
    collect(origin)

    if include_origin is False:
        remove_atoms.discard(origin.GetIdx())
    if origin.IsInRing():
        ring_attachments.add(origin.GetIdx())

    return ring_attachments


def get_scaffold_frags(frag):
    """Get fragments from a disconnected structure.

    This function is used primarily used during molecular fragmentation.

    Parameters
    ----------
    frag : rdkit.Chem.rdchem.Mol
        An rdkit Mol containing disconeccted structures.

    Returns
    -------
    set
        A set of scaffoldgraph.core.Scaffold objects which are unique
        fragments obtained from the disconnected structures in the
        input molecule.

    Notes
    -----
    The function also performs a `partial sanitization` of the input.
    If the molecule fails to sanitize the function will return an
    empty set. This case can occur when an aromatic ring system is
    dissected in a way in which the resultant system is no longer
    aromatic.

    """
    try:
        # frag.ClearComputedProps()
        # frag.UpdatePropertyCache()
        # Chem.GetSymmSSSR(frag)
        partial_sanitization(frag)
    except ValueError as e:
        # This error is caught as dissecting an aromatic ring system,
        # may lead to an undefined state where the resultant system
        # is no longer aromatic. We make no attempt to prevent this
        # but log it for reference.
        # This behaviour may be desirable for a scaffold tree and is
        # equivalent to the behavior of SNG (I believe...)
        logger.debug(e)
        return set()
    frags = {Scaffold(f) for f in GetMolFrags(frag, True, False)}
    return frags


def correct_atom_props(atom):
    """
    Used during fragmentation to correct atom properties where an
    adjacent atom is removed.

    Parameters
    ----------
    atom : rdkit.Chem.rdchem.Atom
        Atom to correct.

    """
    if atom.GetIsAromatic() and atom.GetAtomicNum() != 6:
        atom.SetNumExplicitHs(1)
    elif atom.GetNoImplicit() or atom.GetChiralTag() != CHI_UNSPECIFIED:
        atom.SetNoImplicit(False)
        atom.SetNumExplicitHs(0)
        atom.SetChiralTag(CHI_UNSPECIFIED)


def partial_sanitization(mol):
    """Partially sanitize a molecule.

    Parameters
    ----------
    mol : rdkit.Chem.rdchem.Mol
        Molecule to sanitize.

    """
    SanitizeMol(
        mol, sanitizeOps=SANITIZE_ALL ^
        SANITIZE_CLEANUP ^
        SANITIZE_CLEANUPCHIRALITY ^
        SANITIZE_FINDRADICALS
    )


def get_murcko_scaffold(mol, generic=False):
    """Get the murcko scaffold for an input molecule.

    Parameters
    ----------
    mol : rdkit.rdchem.Chem.Mol
    generic : bool
        If True return a generic scaffold (CSK)

    Returns
    -------
    murcko : rdkit.Chem.rdchem.Mol
        Murcko scaffold.

    """
    murcko = MurckoScaffold.GetScaffoldForMol(mol)
    if generic:
        murcko = MurckoScaffold.MakeScaffoldGeneric(murcko)
    return murcko


def get_annotated_murcko_scaffold(mol, scaffold=None, as_mol=True):
    """
    Return an annotated murcko scaffold where side chains are replaced
    with a dummy atom ('*').

    Parameters
    ----------
    mol : rdkit.Chem.rdchem.Mol
    scaffold : rdkit.Chem.rdchem.Mol, optional
        If a murcko scaffold is already calculated for the `mol`,
        this can be supplied as a template. The default is None.

    as_mol : bool, optional
        If True return rdkit.Chem.rdchem.Mol object else return
        a SMILES string representation. The default is True.

    Returns
    -------
    {str, rdkit.Chem.rdchem.Mol}
        Annotated Murcko scaffold.

    """
    if not scaffold:
        scaffold = MurckoScaffold.GetScaffoldForMol(mol)
    annotated = rdmolops.ReplaceSidechains(mol, scaffold)
    if as_mol:
        return annotated
    if annotated is None:
        return ''
    return MolToSmiles(annotated)


def get_next_murcko_fragments(murcko_scaffold, break_fused_rings=True):
    """
    Fragment a scaffold into its next set of Murcko fragments.

    The fragmenter assumes that a Murcko scaffold is supplied. If not
    the behaviour may be undesirable.

    Parameters
    ----------
    murcko_scaffold : rdkit.Chem.rdchem.Mol
        A Murcko scaffold to fragment
    break_fused_rings : bool, optional
        If True dissect fused rings. The default is True.

    Returns
    -------
    parents : list
        A list of parent scaffolds (next hierarchy [num_rings - 1])

    """
    rdlogger.setLevel(4)

    if break_fused_rings:
        fragmenter = MurckoRingFragmenter()
    else:
        fragmenter = MurckoRingSystemFragmenter()

    parents = [f.mol for f in set(fragmenter.fragment(Scaffold(murcko_scaffold)))]
    rdlogger.setLevel(3)
    return parents


# fragmenter is hierarchical so all fragments may not be returned (fix?).
def get_all_murcko_fragments(mol, break_fused_rings=True):
    """
    Get all possible murcko fragments from a molecule through
    recursive removal of peripheral rings.

    Parameters
    ----------
    mol : rdkit.Chem.rdchem.Mol
    break_fused_rings : bool, optional
        If True dissect fused rings. The default is True.

    Returns
    -------
    list
        A list of Murcko fragments for the input molecule.

    Examples
    --------
    Generating Murcko fragments:

    >>> from rdkit import Chem
    >>> smiles = 'Cc1[nH]cnc1Cn1cccc(-c2ccccc2O)c1=O'
    >>> molecule = Chem.MolFromSmiles(smiles)
    >>> frags = get_all_murcko_fragments(molecule)

    """
    rdlogger.setLevel(4)

    if break_fused_rings:
        fragmenter = MurckoRingFragmenter()
    else:
        fragmenter = MurckoRingSystemFragmenter()

    mol = get_murcko_scaffold(mol)
    rdmolops.RemoveStereochemistry(mol)
    scaffold = Scaffold(mol)
    parents = {scaffold}

    def recursive_generation(child):
        for parent in fragmenter.fragment(child):
            if parent in parents:
                continue
            parents.add(parent)
            recursive_generation(parent)

    recursive_generation(scaffold)
    rdlogger.setLevel(3)
    return [f.mol for f in parents]
